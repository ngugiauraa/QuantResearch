"""A diversified trend-following portfolio across the broker's markets, under the catalogue's hard rules.

    python -m quantresearch.research.multimarket --download   # copy MT5 D1 history (read-only), then run
    python -m quantresearch.research.multimarket              # run on the cached history

Markets: gold, silver, WTI, the S&P 500, the dollar index and the seven major FX pairs, from the Exness MT5 terminal
(D1 bid bars with the broker's spread). History starts 2014 for gold, mid-2018 for silver and FX, 2019 for the
dollar index and mid-2023 for WTI and the S&P 500, so those two barely reach the out-of-sample period.

Four fixed variants, set before any was run, each a new trial in the project trial book:
  mm_tsmom_d1   time-series momentum: the sign of the past 252-day return (Moskowitz, Ooi and Pedersen, 'Time
                Series Momentum', J. Financial Economics 104(2), 2012)
  mm_ema_d1     the sign of EMA(50) minus EMA(200) (the classic 50/200 trend filter; Hurst, Ooi and Pedersen, 'A
                Century of Evidence on Trend-Following Investing', 2017, use similar horizons)
  mm_tsmom_w1   the sign of the past 52-week return, decided on weekly closes
  mm_ema_w1     the sign of EMA(10) minus EMA(40) on weekly closes
Mechanics, identical for all: a signal decided at a close is held from the next day's return; each market is sized
to 10% annualised volatility from a 60-day EWMA of its returns (capped at 4x notional); the size is only traded
when the signal flips or the held size drifts more than 25% from target; each change pays half the broker's
spread on the traded notional (observed; a bar reporting a zero spread uses the market's median non-zero spread). Stressed costs pay three times that. The portfolio is the equal-weight
average of the markets trading that day (equal risk).

The same rules as the catalogue decide it (`catalogue_verdict`): 30+ out-of-sample trades (signal changes), positive
out of sample, positive in the two-year holdout and under stressed costs, a project-wide deflated Sharpe above 0.95,
and beating long-only at the same risk out of sample and in the holdout. With no parameters fitted, "out of sample"
is every day before the holdout once indicators are warm; the yearly results are shown as folds. Long-only for an
FX pair means long the pair as quoted (EURUSD long the euro, USDJPY long the dollar), which is arbitrary, and said so.
Each market inside a portfolio is also recorded in the trial book (kind 'component'): it counts towards N.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from quantresearch.core.config import Paths, paths
from quantresearch.core import timeframes as tfs
from quantresearch.research import stats
from quantresearch.research.trials import DAILY, TrialBook, project_deflated_sharpe

MARKETS = ("XAUUSDm", "XAGUSDm", "USOILm", "US500m", "DXYm", "EURUSDm", "GBPUSDm", "USDJPYm", "AUDUSDm", "USDCADm", "USDCHFm", "NZDUSDm")
VARIANTS = {
    "mm_tsmom_d1": {"kind": "tsmom", "tf": "d1", "lookback": 252},
    "mm_ema_d1": {"kind": "ema", "tf": "d1", "fast": 50, "slow": 200},
    "mm_tsmom_w1": {"kind": "tsmom", "tf": "w1", "lookback": 52},
    "mm_ema_w1": {"kind": "ema", "tf": "w1", "fast": 10, "slow": 40},
}
TARGET_VOL, VOL_SPAN, MAX_LEV, BUFFER, STRESS_X = 0.10, 60, 4.0, 0.25, 3.0
HOLDOUT_YEARS = 2
EQUITY = 100_000.0


def folder(p: Paths):
    return p.external / "multimarket"


def download(p: Paths | None = None, broker=None) -> dict[str, str]:
    """Closed D1 bars for every market, read-only, merged with what is already cached (never shortened)."""
    p = p or paths()
    out = {}
    if broker is None:
        from quantresearch.execution.mt5_adapter import Mt5Broker

        broker = Mt5Broker(symbol="XAUUSDm")
        broker.connect()
    folder(p).mkdir(parents=True, exist_ok=True)
    now = pd.Timestamp.now(tz="UTC")
    for sym in MARKETS:
        try:
            new = pd.DataFrame(broker.rates(sym, "d1"))
            new["ts"] = pd.to_datetime(new["ts"], utc=True)
            new = new[new["ts"] + pd.Timedelta(days=1) <= now]  # Closed days only.
            path = folder(p) / f"{sym}_d1.parquet"
            if path.exists():
                old = pd.read_parquet(path)
                new = pd.concat([old[~old["ts"].isin(new["ts"])], new]).sort_values("ts")
            new.reset_index(drop=True).to_parquet(path, index=False)
            out[sym] = f"ok ({len(new)} days, {new['ts'].iloc[0].date()} to {new['ts'].iloc[-1].date()})"
        except Exception as exc:
            out[sym] = f"failed: {type(exc).__name__}: {exc}"
    return out


def load(p: Paths) -> dict[str, pd.DataFrame]:
    out = {}
    for sym in MARKETS:
        path = folder(p) / f"{sym}_d1.parquet"
        if path.exists():
            out[sym] = pd.read_parquet(path).sort_values("ts").reset_index(drop=True)
    return out


def market_signal(bars: pd.DataFrame, v: dict) -> pd.Series:
    """+1 / -1 / 0 per day, decided at that day's close. Weekly variants decide on weekly closes, held all week."""
    close = bars.set_index("ts")["close"]
    if v["tf"] == "w1":
        week = tfs.bucket(pd.Series(close.index, index=close.index), "w1")
        wk = close.groupby(week.to_numpy()).last()
    else:
        wk = close
    if v["kind"] == "tsmom":
        raw = np.sign(wk / wk.shift(v["lookback"]) - 1)
    else:
        raw = np.sign(wk.ewm(span=v["fast"], adjust=False).mean() - wk.ewm(span=v["slow"], adjust=False).mean())
        raw[wk.expanding().count() < v["slow"]] = np.nan
    if v["tf"] == "w1":
        # A week's signal is known at its close (the next Saturday 00:00 UTC) and acted on at the first daily close after.
        known = pd.Series(raw.to_numpy(), index=pd.DatetimeIndex(raw.index) + pd.Timedelta(days=7))
        return known.reindex(known.index.union(close.index)).ffill().reindex(close.index).fillna(0.0)
    return raw.reindex(close.index).fillna(0.0)


def market_returns(bars: pd.DataFrame, signal: pd.Series, stress: float = 1.0, long_only: bool = False) -> pd.DataFrame:
    """Daily strategy return of one market, with costs and the trade count."""
    b = bars.set_index("ts")
    r = b["close"].pct_change()
    vol = r.ewm(span=VOL_SPAN, min_periods=VOL_SPAN).std() * np.sqrt(252)
    sig = pd.Series(1.0, index=b.index) if long_only else signal.reindex(b.index).fillna(0.0)
    target = (sig * (TARGET_VOL / vol)).clip(-MAX_LEV, MAX_LEV).where(vol.notna(), 0.0)
    held = np.zeros(len(target))
    trades = np.zeros(len(target))
    prev = 0.0
    for i, t in enumerate(target.to_numpy()):
        flip = np.sign(t) != np.sign(prev)
        drift = prev != 0 and abs(t - prev) > BUFFER * abs(prev)
        if flip or drift:
            trades[i] = 1.0 if flip and t != 0 else 0.0
            prev = t
        held[i] = prev
    held_s = pd.Series(held, index=b.index)
    # MT5 reports a zero spread on many older D1 bars (gold especially); a free fill would flatter the result, so a
    # zero is replaced by the market's own median non-zero spread to date (or its first non-zero one before that).
    spread = b["spread"].where(b["spread"] > 0)
    spread = spread.fillna(spread.expanding().median()).bfill()
    half_spread = (spread / 2 / b["close"]).rolling(20, min_periods=1).median()
    cost = held_s.diff().abs().fillna(held_s.abs()) * half_spread * stress
    ret = held_s.shift(1) * r - cost.shift(0)
    return pd.DataFrame({"ret": ret.fillna(0.0), "held": held_s, "trades": trades})


def _sharpe(x: pd.Series) -> float:
    return float(stats.sharpe(x.to_numpy())) if len(x) > 2 else float("nan")


def _bench(strategy: pd.Series, buy_hold: pd.Series) -> dict:
    days = strategy.index.intersection(buy_hold.index)
    if len(days) < 20:
        return {"available": False, "beats": False, "reason": "fewer than 20 common days"}
    s, g = strategy.loc[days], buy_hold.loc[days]
    return {"available": True, "days": int(len(days)), "strategy_sharpe": _sharpe(s), "buy_hold_sharpe": _sharpe(g),
            "beats": bool(_sharpe(s) > _sharpe(g) and s.std() > 0)}


def evaluate_variant(name: str, data: dict[str, pd.DataFrame], holdout_start: pd.Timestamp) -> dict:
    v = VARIANTS[name]
    per, obs, stress, bh, trades = {}, {}, {}, {}, {}
    for sym, bars in data.items():
        sig = market_signal(bars, v)
        o, s, h = market_returns(bars, sig), market_returns(bars, sig, STRESS_X), market_returns(bars, sig, long_only=True)
        warm = o.index[(o["held"] != 0).to_numpy()]
        start = warm[0] if len(warm) else o.index[-1]
        o, s, h = o[o.index >= start], s[s.index >= start], h[h.index >= start]
        obs[sym], stress[sym], bh[sym], trades[sym] = o["ret"], s["ret"], h["ret"], o["trades"]
        oos, hold = o.index < holdout_start, o.index >= holdout_start
        per[sym] = {"first_day": str(start.date()), "oos_days": int(oos.sum()), "holdout_days": int(hold.sum()),
                    "oos_trades": int(o["trades"][oos].sum()), "oos_sharpe": _sharpe(o["ret"][oos]), "holdout_sharpe": _sharpe(o["ret"][hold]),
                    "oos_return": float(o["ret"][oos].sum()), "holdout_return": float(o["ret"][hold].sum()),
                    "holdout_return_stress": float(s["ret"][hold].sum()),
                    "beats_buy_and_hold_oos": _bench(o["ret"][oos], h["ret"][oos]), "beats_buy_and_hold_holdout": _bench(o["ret"][hold], h["ret"][hold])}
    port = pd.DataFrame(obs).mean(axis=1, skipna=True).dropna()
    port_s = pd.DataFrame(stress).mean(axis=1, skipna=True).dropna()
    port_bh = pd.DataFrame(bh).mean(axis=1, skipna=True).dropna()
    n_trades = pd.DataFrame(trades).sum(axis=1)
    oos, hold = port.index < holdout_start, port.index >= holdout_start
    years = {str(y): {"return": float(port[oos][port[oos].index.year == y].sum()), "sharpe": _sharpe(port[oos][port[oos].index.year == y])}
             for y in sorted(set(port[oos].index.year))}
    bench_oos, bench_hold = _bench(port[oos], port_bh[port_bh.index < holdout_start]), _bench(port[hold], port_bh[port_bh.index >= holdout_start])
    report = {
        "strategy": name, "grid": {}, "defaults": v, "catalogue": True, "family": "multi_market", "created_at": datetime.now(timezone.utc).isoformat(),
        "markets": per, "folds_by_year": years, "holdout_start": str(holdout_start.date()),
        "walk_forward": {"oos_sharpe": _sharpe(port[oos]), "deflated_sharpe": {"dsr": stats.probabilistic_sharpe(port[oos].to_numpy(), 0.0)},
                         "out_of_sample": {"trades": int(n_trades[n_trades.index < holdout_start].sum()), "net_profit": float(EQUITY * port[oos].sum()),
                                           "first_day": str(port.index[0].date()), "days": int(oos.sum())}},
        "holdout": {"scenarios": {"observed": {"net_profit": float(EQUITY * port[hold].sum()), "sharpe": _sharpe(port[hold])},
                                  "stress": {"net_profit": float(EQUITY * port_s[port_s.index >= holdout_start].sum())}}},
        "risk_matched_benchmark": {"out_of_sample": bench_oos, "holdout": bench_hold,
                                   "beats_both": bool(bench_oos.get("beats") and bench_hold.get("beats")),
                                   "method": "Sharpe of the portfolio vs an equal-risk long-only portfolio of the same markets over the same days"},
    }
    return report, port[oos].to_numpy(), {sym: _sharpe(obs[sym][obs[sym].index < holdout_start]) for sym in obs}


def run(p: Paths | None = None) -> dict:
    from quantresearch.research.catalogue import catalogue_verdict

    p = p or paths()
    data = load(p)
    if not data:
        raise FileNotFoundError("No multi-market history; run with --download first")
    end = max(b["ts"].iloc[-1] for b in data.values())
    holdout_start = (end - pd.DateOffset(years=HOLDOUT_YEARS)).normalize()
    out_dir = p.research / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    results, returns = {}, {}
    book = TrialBook(p)
    try:
        for name in VARIANTS:
            report, oos_daily, comp = evaluate_variant(name, data, holdout_start)
            returns[name] = oos_daily
            book.record_report(report, f"research/reports/{name}_latest.json")
            for sym, sh in comp.items():
                book._upsert(f"{name}:{sym}", json.dumps(VARIANTS[name], sort_keys=True), "component",
                             sh / DAILY if np.isfinite(sh) else None, f"research/reports/{name}_latest.json")
            book.con.commit()
            results[name] = report
        for name, report in results.items():  # Judged against the book after all four (and their components) are in.
            project = project_deflated_sharpe(returns[name], book)
            report["project_multiple_testing"] = {**project, "book": book.summary()}
            report["catalogue_verdict"] = catalogue_verdict(report, project)
            (out_dir / f"{name}_latest.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
            (p.research / "catalogue_returns").mkdir(parents=True, exist_ok=True)
            np.save(p.research / "catalogue_returns" / f"{name}.npy", returns[name])
    finally:
        book.close()
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Multi-market trend portfolio under the catalogue's rules.")
    ap.add_argument("--download", action="store_true")
    args = ap.parse_args(argv)
    if args.download:
        for k, v in download().items():
            print(k, v)
    for name, r in run().items():
        v = r["catalogue_verdict"]
        print(name, v["label"], {k: r["walk_forward"][k] for k in ("oos_sharpe",)}, "trades", r["walk_forward"]["out_of_sample"]["trades"],
              "holdout", round(r["holdout"]["scenarios"]["observed"]["net_profit"]), "stress", round(r["holdout"]["scenarios"]["stress"]["net_profit"]),
              "beats B&H", r["risk_matched_benchmark"]["beats_both"], "DSR", round(r["project_multiple_testing"]["dsr"], 3))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
