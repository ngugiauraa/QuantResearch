# QuantResearch

An integrated XAUUSD research, market-intelligence, machine-learning and trading platform: a verified data
warehouse, seven strategy engines, eleven trading skills, a Time Machine, an independent risk engine, MetaTrader 5
integration, and one dashboard.

> **Status: all phases are implemented and tested, and no strategy has qualified for live trading.** Of the seven
> engines, four found no edge and three were weak or not significant once the number of variants tried is counted.
> No ML model beat a constant baseline. Those are valid research results. **Live trading is off by default and has
> never been run.** MT5 is tested against a mock only, because no terminal was running.
>
> Research software, not financial advice. A backtest does not qualify anything for live use.

**Record date:** 2026-11-02. **Instrument:** XAUUSD. **Runtime:** Python 3.12 and Node 22 on Windows.

## Table of contents

1. [Quick start](#1-quick-start)
2. [Research results](#2-research-results)
3. [Implementation status](#3-implementation-status)
4. [Known limitations and remaining work](#4-known-limitations-and-remaining-work)
5. [Architecture](#5-architecture)
6. [Repository tree and module responsibilities](#6-repository-tree-and-module-responsibilities)
7. [Historical data infrastructure](#7-historical-data-infrastructure)
8. [Data schemas and storage design](#8-data-schemas-and-storage-design)
9. [Data sources and coverage](#9-data-sources-and-coverage)
10. [Data-quality findings](#10-data-quality-findings)
11. [Bid and ask data](#11-bid-and-ask-data)
12. [Point-in-time rules](#12-point-in-time-rules)
13. [Feature engineering](#13-feature-engineering)
14. [Backtesting engine](#14-backtesting-engine)
15. [Time Machine](#15-time-machine)
16. [The eight strategy engines](#16-the-eight-strategy-engines)
17. [Research methodology](#17-research-methodology)
18. [The eleven trading skills](#18-the-eleven-trading-skills)
19. [Machine learning](#19-machine-learning)
20. [Knowledge and research layer](#20-knowledge-and-research-layer)
21. [Decision engine](#21-decision-engine)
22. [Risk engine](#22-risk-engine)
23. [MT5 integration and trading modes](#23-mt5-integration-and-trading-modes)
24. [API](#24-api)
25. [Dashboard](#25-dashboard)
26. [Configuration and environment variables](#26-configuration-and-environment-variables)
27. [Dependencies](#27-dependencies)
28. [Commands](#28-commands)
29. [Tests and actual results](#29-tests-and-actual-results)
30. [Bugs found and fixed](#30-bugs-found-and-fixed)
31. [External services and credentials](#31-external-services-and-credentials)
32. [Security](#32-security)
33. [Operational workflows](#33-operational-workflows)
34. [Recovery and troubleshooting](#34-recovery-and-troubleshooting)
35. [Project rules](#35-project-rules)
36. [History](#36-history)
37. [Autonomous strategy discovery](#37-autonomous-strategy-discovery)

---

## 1. Quick start

Run from the repository root in Git Bash:

```bash
source venv/Scripts/activate
python -m pip install -r requirements-lock.txt      # exact versions; requirements.txt lists direct ones
(cd frontend && npm install && npx next build)      # builds the dashboard once
```

### First-time data setup

The market data is not in the repository (`data/` is git-ignored), so a fresh clone has none. Research commands, the Time Machine
and the dashboard charts need it, and without it they stop with a message pointing here. Build it once, in this order
(each step can be interrupted and rerun, and continues where it left off):

```bash
PYTHONPATH=src python -m quantresearch.warehouse.fetch_months m1 --workers 2   # downloads about 280 monthly M1 files
PYTHONPATH=src python -m quantresearch.warehouse.build --start 2003-05         # builds M1 to MN1 bars from them
PYTHONPATH=src python -m quantresearch.warehouse.audit                         # completeness report
PYTHONPATH=src python -m quantresearch.external.store                          # macro and cross-market data
```

The download is the slow part and depends on your connection; there is no fixed time estimate for the M1 download. The optional tick
download (`fetch_months tick`, roughly 10 minutes per month and about 15 hours in total) is separate and **not needed** for bars; see
[Section 9](#9-data-sources-and-coverage). To keep the data somewhere else, set `QR_DATA_ROOT`.

### Start the server

```bash
PYTHONPATH=src python -m quantresearch.api.server   # then open http://127.0.0.1:8000
```

The server runs locally and has no login, so it must not be exposed to a network (the one optional exception is the
tunnel below, with its warnings). It starts on a simulated paper account. Nothing can reach a broker without the steps in
[Section 23](#23-mt5-integration-and-trading-modes).

### Cloud deployment (optional)

The Python backend cannot move to Cloudflare: it needs the local Parquet warehouse and MetaTrader 5 on Windows. What can be
hosted is the dashboard, with a Cloudflare Tunnel carrying its requests back to the API on your PC. **Cloudflare Access (a
free login) must sit in front of both the dashboard and the tunnel.**

```
Internet user -> Cloudflare Access (login) -> Pages (dashboard)
                                           -> Tunnel -> 127.0.0.1:8000 (API)
```

> **Security: the API has no authentication of its own. Cloudflare Access is the only thing protecting it.** Never disable
> Access, never share the tunnel URL, and never set `QR_CORS_ORIGINS` to `*` (the server refuses to start if you do).
> "Don't share the URL" is not a security model. `NEXT_PUBLIC_API_URL` is compiled into the dashboard's JavaScript, so a
> dashboard without Access would hand the API address to anyone who opens it.

**Step 1: create the tunnel** (`cloudflared` on Windows):

```
cloudflared tunnel login
cloudflared tunnel create quantresearch
```

Then copy `config/cloudflare-tunnel/config.yml` to `%USERPROFILE%\.cloudflared\config.yml` and fill in the tunnel ID and hostname.

**Step 2: create two Access applications** at one.dash.cloudflare.com (Zero Trust, free up to 50 users):

- App 1: the dashboard's hostname. Use a custom domain such as `dashboard.yourdomain.com`, and also `*.quantresearch-dashboard.pages.dev`,
  because every Pages deployment has its own address that a bare-domain application would not cover.
- App 2: the API hostname, for example `api.quantresearch.yourdomain.com`.
- Policy for both: Allow, Email, your address (or a list). Session duration 24 hours.

**Step 3: lock CORS to the dashboard.** Start the API with exactly the dashboard's origin:
`QR_CORS_ORIGINS=https://dashboard.yourdomain.com PYTHONPATH=src python -m quantresearch.api.server`.
Local development origins stay allowed; there is no wildcard. The API also allows credentials so the browser can send the Access cookie.

**Step 4: give the build the API address, without committing it.** `NEXT_PUBLIC_API_URL=https://api.quantresearch.yourdomain.com`.
For the GitHub workflow (`.github/workflows/deploy-frontend.yml`, which runs on a push to `main` that touches `frontend/`) it is a repository
**secret** named `NEXT_PUBLIC_API_URL`, next to `CLOUDFLARE_API_TOKEN` (permission: Cloudflare Pages, Edit) and `CLOUDFLARE_ACCOUNT_ID`.
It is a GitHub secret rather than a Pages dashboard variable because the workflow builds the site itself, and Pages dashboard variables are
only used when Cloudflare runs the build. For a local build use `frontend/.env.local` (git-ignored).

**Step 5: run it** (`cloudflared tunnel run quantresearch`), open the API address once to sign in, then open the dashboard. The full
walkthrough, including a checklist to confirm the lock actually holds, is in `config/cloudflare-tunnel/README.md`.

What this guarantees: the tunnel address is not in the git repository; it is in the JavaScript bundle, which is only reachable after the
Access login; and the tunnel is behind its own Access login, so reading the address is not enough. CORS stops other websites calling the API from
a browser, but not `curl`: a caller needs the address **and** a valid Access session or token.

**Step 6 (optional, recommended): make the API check the Access token itself.** Access normally stops unauthenticated requests at Cloudflare's
edge; this adds a second lock in the API, so a misconfigured Access application or a hostname added to the tunnel later still cannot reach it.
Start the API with `QR_VERIFY_CF_ACCESS=true`, `QR_CF_ACCESS_AUD=<the AUD tag of the API's Access application>` and `QR_CF_ACCESS_TEAM=<your team>`
(Section 26). Only RS256 tokens issued by your team, for that audience and not expired, are accepted. With it on, use the dashboard through the
tunnel only: `http://127.0.0.1:8000` carries no token and is refused. Leave it off for ordinary local use.

The local workflow above is unchanged: with `NEXT_PUBLIC_API_URL` unset, the dashboard finds the API on its own.

---

## 2. Research results

**Read this before reading any number as profit.** Gold rose about 303 % over the out-of-sample period and 39 % over
the holdout, so long-biased rules were flattered. Stated results use each period's observed bid and ask.

Walk-forward used 17 folds: train 4 years, test 12 months, rolling, from 2003-05 to 2024-10. The holdout is
2024-10-30 to 2026-10-30. Each run starts with $100,000 and risks 0.5 % per trade. Fills were simulated on each
strategy's own signal bars.

| Strategy | Result | OOS trades | OOS net $ | OOS Sharpe (95 % CI) | Deflated Sharpe | Profit factor | Max DD | Holdout net $ | Holdout, stressed costs $ |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Mean Reversion | No edge | 708 | −44,880 | −0.77 (−1.24, −0.34) | 0.000 | 0.72 | 48.6 % | −6,081 | −7,627 |
| Trend and Momentum | Weak | 1,206 | +100,105 | 0.47 (0.02, 0.88) | 0.004 | 1.19 | 18.5 % | +3,442 | +1,767 |
| Statistical Arbitrage | No edge | 498 | −18,201 | −0.24 (−0.72, 0.21) | 0.000 | 0.89 | 26.3 % | −6,464 | −8,085 |
| Behavioral Bias | No edge | 3,956 | −97,369 | −1.84 (−2.31, −1.41) | 0.000 | 0.67 | 97.6 % | −18,484 | −32,107 |
| ETF and Turnaround | Weak | 159 | +5,285 | 0.13 (−0.33, 0.61) | 0.000 | 1.11 | 14.2 % | +4,141 | +3,819 |
| Seasonal and Calendar | No edge | 2,885 | −34,193 | −1.26 (−1.75, −0.79) | 0.000 | 0.73 | 34.6 % | −3,762 | −23,428 |
| Overreaction and Gaps | Weak | 2,070 | +10,066 | 0.12 (−0.30, 0.56) | 0.000 | 1.02 | 15.9 % | −13,314 | −17,901 |

**What this supports.**

- **Trend is the only one worth further work.** It made money on both sides (long +65,747, short +34,358) and its
  confidence interval just clears zero. But 306 training trials were tried, and the deflated Sharpe, which allows for
  that, is 0.004 against the 0.95 needed. The holdout was only slightly positive, with a Sharpe of 0.25 during a 39 %
  rise in gold. It is a candidate for more research, not for trading.
- **ETF and Turnaround's** holdout looks good, but on only 25 trades. Treat it as an anecdote.
- **Overreaction's** selection flipped to following rather than fading, and it lost in the holdout. This is what an
  unstable result looks like.
- **Behavioral's** 97.6 % drawdown shows that fading shocks on M15 loses steadily to costs, not that the engine failed.

**Hypothesis tests** (before the holdout, with false-discovery-rate correction across each family). Only 2 of 57 tests
were significant, both in the seasonal family: Friday (mean excess 5.8e-05 per hour, adjusted p 0.0016) and the Asia
session (3.1e-05, adjusted p 0.0016). They are real statistical patterns but too small to pay the spread, which is why
that engine loses. **Statistical significance is not profitability.**

**Other findings.** Gold and silver were not cointegrated over 2010 to 2026 (p 0.66). Log gold is non-stationary. Daily
returns have almost no autocorrelation (0.0006) but absolute returns do (0.12), so volatility clusters while direction
does not.

**Combining engines on the holdout** did not create an edge. Each single-engine row matches that engine's own holdout
figure exactly.

| Experiment | Trades | Net $ | Sharpe | Max DD |
|---|---:|---:|---:|---:|
| All six H1 engines, majority vote | 816 | −13,921 | −0.77 | 20.7 % |
| At least two votes | 180 | +4,669 | 0.50 | 6.1 % |
| Trend with FOMC and spread gates | 68 | +3,205 | 0.24 | 7.2 % |
| Majority, through the risk engine | 308 | −3,475 | −0.33 | 10.7 % |
| Majority, with veto by an unpromoted ML model | 813 | −12,900 | −0.71 | 19.9 % |

**Machine learning.** No variant met the promotion gate (best log loss 0.6936 against a constant's 0.6940, AUC 0.513 to
0.519). Conditions also failed to predict trade outcomes for trend, mean reversion and overreaction. Nothing is promoted.

**Honest caveats on these numbers.**

- The holdout has been run more than once while fixing software defects. No strategy rule or parameter was changed in
  response to holdout results, but it can no longer be called completely unseen.
- Simulated on bars, which cannot show the path inside a bar. Stops are counted first, which is conservative.
- Spreads are Dukascopy's and were not calibrated to your broker. Early-year spreads look processed ([Section 11](#11-bid-and-ask-data)).
- Overnight financing is set to zero. Real swap costs would hurt long holds such as trend.

Full reports: `data/research/reports/*_latest.json`, and the Quantitative Research page of the dashboard.

---

## 3. Implementation status

| Component | Status | Evidence |
|---|---|---|
| Month-end download defect | **Fixed and repaired** | 282 of 282 month ends present |
| M1 bid and ask, 2003-05 to 2026-10 | Complete | 564 monthly files, 0 failures, audit passes |
| M5, M15, M30, H1, H4, D1, MN1 | Complete | Aggregated from M1 |
| Tick history | **In progress** | Downloading newest first ([Section 9](#9-data-sources-and-coverage)) |
| S1 | Complete where ticks exist | Built on demand from ticks |
| Completeness audit, quarantine, manifest | Complete | Fails on the old defect, by test |
| Features: technical, structure, microstructure, macro | Complete | Each tested; truncation-invariant |
| Event-driven backtester | Complete | Hand-calculated tests |
| Look-ahead integrity test | Complete, **passing** | 15 real-data cutoffs; catches planted cheats |
| Time Machine replay and viewer | Complete | Browser tests; used on real data |
| Seven strategy engines and reports | Complete | All run on real data |
| Risk engine | Complete | Each limit tested |
| Paper trading | Complete on replayed data | No live feed without MT5 |
| MT5 adapter | **Mock-tested only** | No terminal was running |
| Live trading | **Never run; off by default** | Refused without activation, by test |
| Eleven skills | Integrated | See [Section 18](#18-the-eleven-trading-skills) |
| Outcome Simulator (original Time Machine) | Complete | Reproduces upstream's published result |
| External data | Mostly complete | 21 of 23 sources |
| ML pipeline and registry | Complete | Nothing qualified for promotion |
| Knowledge layer | Complete | Your own literature must be added |
| API and dashboard | Complete | 31 browser checks |

---

## 4. Known limitations and remaining work

**Not done, and needs you or external access:**

| Item | Why | What it needs |
|---|---|---|
| Real MT5 connection test | No terminal was running, and I did not launch your terminal or touch a broker account | Open your terminal, set `QR_BROKER=mt5`, and test read-only first |
| Broker-specific costs | Backtests use provider spreads | Compare with your MT5 spreads and swap |
| SEC-based skills on live data | SEC requires a real name and email, which is never invented | Set `SEC_USER_AGENT` |
| Your literature in the knowledge layer | None was supplied | Add files to `data/research/library/<kind>/` |

**Data not available from free verified sources:**

| Gap | Effect |
|---|---|
| ETF holdings and flows | The GLD file came back as a PDF. **ETF flows are not tested**, so Strategy 5 covers turn-of-month and first notice only |
| Gold futures prices | Stooq returned a block page. Rollover price effects are not observed |
| Central-bank gold demand | No source. Not included |
| CPI and payroll release times | The BLS schedule refused automated access (HTTP 403), which was not bypassed. Only FOMC is tested as an event |
| First-release macro values | Needs ALFRED vintages and an API key. FRED levels are revised, so **no macro-surprise feature exists** |
| S&P 500 and WTI CFDs | The download tool fails on these instruments. VIX and WTI are available from FRED instead |
| Historical news | GDELT gives partial recent headlines only. The platform runs without news |
| First-notice dates | Approximated by rule, not from the exchange calendar |

**Functional limits:**

- **Live bars need MT5.** With `QR_BROKER=mt5`, charts, signals and the overview add the broker's closed M1 bars after the warehouse's last bar (flag 16 marks them). Without MT5, charts end at the last build.
- Without MT5, the paper broker's price is the last stored bar, and the dashboard says so.
- Alerts run when checked. There is no background scheduler, by design of the upstream skill.
- Deep learning (LSTM, GRU, transformers) and financial NLP are **not implemented**, deliberately: baselines showed no signal, and your specification requires measured value first.
- The knowledge layer retrieves and attributes. It does not write answers.
- Monitoring is the health endpoint and dashboard. There is no alerting if the server stops.
- No migration tool: SQLite tables are created if missing.
- The legacy `data/processed/` archive and first-generation modules are kept but superseded. `research_db.py` still reads only the old archive.
- D1 and MN1 before 2003-05 remain only in the legacy archive, with unverified provenance and 119 quarantined rows.

**Fidelity deviation for your attention:** the original skills never allow an order without a typed yes. Automated mode, which you require, departs from that. It exists only behind separate activation.

---

## 5. Architecture

```text
Market data (Dukascopy, FRED, CFTC, Federal Reserve, GDELT)
   -> Warehouse: immutable sources, audit, quarantine, Parquet bars
   -> Point-in-time layer and features
   -> Strategy engines + skills + ML + knowledge
   -> Decision engine (proposes only)
   -> Independent risk engine (approves, reduces or rejects)
   -> Order router -> paper broker or MT5
   -> Audit log with hash chain
FastAPI serves all of this to the Next.js dashboard. Backtests and replays use the same engine and can use the same risk engine.
```

**Key decisions.**

- One event engine serves backtests and replays, so they cannot disagree. A test proves identical results.
- Strategies are causal column generators. Execution is shared, so the seven engines differ only in logic.
- Higher timeframes are aggregated from M1 so all timeframes agree exactly.
- SQLite for application state. PostgreSQL was not needed: one local process, no concurrent writers.
- Static Next.js export served by FastAPI, so one process runs the whole dashboard.

---

## 6. Repository tree and module responsibilities

```text
QuantResearch/
├── config/gold_data_catalog.json
├── pyproject.toml, requirements.txt, requirements-lock.txt, README.md
├── frontend/                       Next.js dashboard
│   ├── src/app/                    shell and styles
│   ├── src/sections/               one file for each of the ten sections
│   ├── src/components/             charts and shared UI
│   ├── src/lib/api.ts              API client
│   └── e2e/run.mjs                 browser workflow tests
├── tests/                          pytest suite and upstream fixtures
├── data/                           ignored by Git, except .gitkeep
│   ├── warehouse/                  bars, csv, ticks, bi5, quarantine, external, reports, manifest.sqlite
│   ├── research/                   reports, experiments.sqlite, library/
│   ├── models/                     registry.sqlite and model files
│   ├── app/                        risk, paper, audit, knowledge and skill-run databases
│   ├── raw/, processed/, synthetic/  first-generation archive, kept and unmodified
└── src/quantresearch/
    ├── core/                       paths, timeframes
    ├── warehouse/                  fetch_months, fetch, dukascopy, build, audit, calendar, store
    ├── external/                   sources, store
    ├── features/                   pit, technical, structure, microstructure, registry
    ├── sim/                        engine, costs, metrics, reference
    ├── timemachine/                replay, lookahead, viewer, orchestrate
    ├── strategies/                 base, engines, registry
    ├── research/                   stats, lab, experiment
    ├── skills/                     upstream/ (original sources), xauusd, outcome, registry
    ├── ml/                         pipeline, train_cli
    ├── knowledge/                  store
    ├── decision/                   engine
    ├── risk/                       engine
    ├── execution/                  broker, mt5_adapter, router, secrets, paper_session
    ├── discovery/                  autonomous strategy discovery (research only; see Section 37)
    ├── api/                        server, services, discovery_api
    └── (first generation)          market_data, ingestion, session_audit, data_catalog, data_sources,
                                    dukascopy_download, data_manifest, resample, coverage_audit, data_quality,
                                    consolidate, raw_validator, historical_gold, quality_pipeline, parquet_audit,
                                    research_db, research_cli, feature_engineering
```

The first-generation modules still work and their tests still pass, but the warehouse supersedes them. `dukascopy_download.py` carries the month-end fix and supplies the pinned tool version.

---

## 7. Historical data infrastructure

**The defect that was fixed.** The old downloader passed each month's last day as an exclusive end date, so the last
day of every month was lost: there were no M1 rows on the 31st at all, and every validator still passed. This would
have ruined month-end research and created false price gaps.

**The repair.**

- The end date is now exclusive by design, and a month is rejected if it stops before its last trading day.
- All M1 history was downloaded again into a **new location**. The old archive was not touched.
- **Every one of the 7,713,120 old rows matches the new data exactly, and none is missing.** 265,661 rows were recovered, 151,741 of them on the 31st.

**Pipeline.**

1. `fetch_months`: whole months through the pinned `dukascopy-node@1.51.1`. Verified, gzip-compressed and checksummed, then recorded in the manifest.
2. `fetch`: the native client for single provider files. Slow when that feed is degraded (about 30 % success on 2026-11-01), so it is a fallback.
3. `build`: merges bid and ask, quarantines invalid rows unchanged, flags oddities, aggregates, and writes atomically.
4. `audit`: expected trading days, month ends, duplicates, ordering, and comparison with the old archive.
5. `store`: the read API. `load_bars(as_of=T)` cannot return an unclosed bar.

Downloads resume, retry, stop on HTTP 403, and stop if free disk falls below the limit.

---

## 8. Data schemas and storage design

**Bars:** `data/warehouse/bars/xauusd/<tf>/year=Y/month=M/bars.parquet`

| Column | Meaning |
|---|---|
| `ts` | Bar **open** time, UTC |
| `bid_open/high/low/close`, `ask_open/high/low/close` | Both sides. Ask stays null if unobserved; it is never estimated here |
| `bid_volume`, `ask_volume` | Provider activity units, not market volume |
| `m1_count` | Source M1 bars |
| `spread_mean`, `spread_max` | Observed close spreads |
| `flags` | 1 no ask, 2 no bid, 4 crossed, 8 spike. Flagged rows are kept |

`store.load_bars` adds `close_ts`, mid `open/high/low/close` and total `volume`.

**Ticks:** `data/warehouse/ticks/year=Y/month=M/ticks.parquet`: `ts, ask, bid, ask_volume, bid_volume`.

**Events (point in time):** `data/warehouse/external/events/*.parquet`: `series, event_ts, published_ts, ingested_ts, revision, value, source, pub_basis`.

**Application databases (SQLite):**

| File | Tables |
|---|---|
| `warehouse/manifest.sqlite` | `files`: key, status, bytes, records, sha256, attempts, error, time |
| `app/risk.sqlite` | `state`, `decisions` |
| `app/audit.sqlite` | `audit`, with a hash chain |
| `app/paper.sqlite` | `paper`, `paper_trades` |
| `app/knowledge.sqlite` | `docs`, `fts` (FTS5) |
| `app/skills.sqlite` | `runs` |
| `research/experiments.sqlite` | `experiments` |
| `models/registry.sqlite` | `models` |

---

## 9. Data sources and coverage

| Timeframe | Rows | From | To | Source |
|---|---:|---|---|---|
| M1 | 7,978,781 | 2003-05-05 | 2026-10-30 | Provider, bid and ask |
| M5 | 1,670,219 | 2003-05-05 | 2026-10-30 | Aggregated |
| M15 | 563,496 | 2003-05-05 | 2026-10-30 | Aggregated |
| M30 | 282,749 | 2003-05-05 | 2026-10-30 | Aggregated |
| H1 | 141,510 | 2003-05-05 | 2026-10-30 | Aggregated |
| H4 | 37,708 | 2003-05-05 | 2026-10-30 | Aggregated |
| D1 (UTC day) | 7,315 | 2003-05-05 | 2026-10-30 | Aggregated |
| D1NY (17:00 New York day) | 6,089 | 2003-05-04 | 2026-10-29 | Aggregated |
| MN1 | 282 | 2003-05 | 2026-10 | Aggregated |
| S1 | On demand | Where ticks exist | | Built from ticks |
| Tick | **Partial; see below** | | | Provider |

**Ticks.** The download runs newest first with a 10 GB free-disk guard, at about 10 minutes per month and about 60 MB
per month. At completion of this record, about a year of recent months was stored, with 2010-09 onward queued. The
full run needs roughly 15 hours more. Check progress in `data/warehouse/tick_months.log`. Rerunning the command resumes it. The first-generation tick CSVs (2003-05 to 2010-08, with 18 missing months) remain in `data/raw/` and have not been imported, since they lack each month's last day.

**External data** (`data/warehouse/external/status.json`):

| Source | Series | Status | Timestamps |
|---|---|---|---|
| FRED | DFF, DGS2, DGS10, DFII10, T10YIE, DTWEXBGS, VIXCLS, DCOILWTICO, CPIAUCSL, PCEPI, PAYEMS, UNRATE, GDP | OK | **Assumed lags**, marked as such. Revised values |
| Federal Reserve | FOMC decisions | OK, 195 | Customary 14:00 New York |
| CFTC | Gold positioning, 2006 on | OK, 4,256 rows | Friday 15:30 publication |
| Dukascopy | Silver, EURUSD, GBPUSD, USDJPY, dollar index, T-bond (H1) | OK | Bar close |
| Dukascopy | S&P 500, WTI CFDs | **Failed** | The tool errors on these |
| GDELT | Headlines | OK, optional | Time first seen. Rate-limited |

---

## 10. Data-quality findings

**The audit passes:** 6,072 of 6,072 expected trading days and 282 of 282 month ends are present. There are no
duplicate timestamps, no out-of-order bars, no rejected rows, no crossed quotes and no missing asks. Two bars lack a bid.

| Finding | Detail |
|---|---|
| Sparse early data | 138 "thin" days, mostly from 2003, where a day has under half the median bars. Early research rests on thin data |
| Observed closures | Christmas, New Year, Good Friday, and the Friday before a Saturday Christmas (2021-12-24 confirms it) |
| Daily break | 17:00 to 18:00 New York appears from 2013, not before. The calendar models this |
| Flat minutes | The tool drops minutes with no quotes, so a missing minute can mean no activity |
| Volume | Provider activity units. Scale changes across years, so do not compare levels across regimes |
| Price outliers | Prior outliers were mostly the month-end artefact. **Prices were not verified against a second source** |
| Spike flag | Zero bars met the rule (a 1.5 % M1 move reversed at once) |
| Ticks and M1 | Rebuilt M1 from ticks matched native bars on all 6,780 bars tested (2026-10-12 to 16), both sides |
| Old and new M1 | All 7,713,120 overlapping rows are identical |

---

## 11. Bid and ask data

**Observed bid and ask now cover all M1 history**, and every timeframe built from it.

| Period | Median spread | 95th pct | Max | Note |
|---|---:|---:|---:|---|
| 2003 to 2009 | 0.41 to 0.49 | 0.56 to 0.58 | about 0.71 | **Few repeated values and a hard ceiling. Looks provider-processed** |
| 2010 to 2013 | 0.35 to 0.51 | 0.54 to 0.72 | 1 to 3 | |
| 2014 to 2019 | 0.24 to 0.31 | 0.30 to 0.40 | about 3 | A ceiling near 3 suggests clipping |
| 2020 | 0.40 | 1.17 | 14.5 | March 2020 |
| 2021 to 2024 | 0.33 to 0.38 | 0.41 to 0.49 | 4 to 6 | |
| 2025 | 0.58 | 0.86 | 14.7 | |
| 2026 | 0.67 | 1.03 | 12.6 | Wider as the gold price rose |

**These are observed at the provider, not your broker.** Calibrate before trusting any cost-sensitive result. Each fill
records `cost_source` (`observed_tick`, `observed_ask_candle` or `assumed`). All research fills are `observed_ask_candle`.
An assumed spread is used only if configured, and is labelled. With no ask and no assumption the engine refuses to run.

---

## 12. Point-in-time rules

1. A bar's values are known at its **close**, never at its open.
2. External values are visible from `published_ts`. `as_of` shows the latest revision published by then.
3. Higher-timeframe joins use bars closed by the base bar's close.
4. Previous-day levels appear only after that day has ended.
5. Swings appear only after their confirmation bars.
6. A published calendar (FOMC, trading days) may be known in advance.
7. Models refuse to predict inside their training period, unless a test is checking exactly that.

**The mandatory look-ahead test** (`timemachine/lookahead.py`): run through T, truncate at T, re-run, compare every
feature, event and equity point. The only exclusion is fills on the bar opening exactly at T, which is counted in the
report. No nondeterminism is allowed: the engine uses no random numbers or wall clock.

Results: **passed at 15 real-data cutoffs**, including month ends, DST changes, the 2008 crisis, April 2013, March 2020,
October 2026, and on real ticks. It also passes for all seven strategies and a full orchestrated experiment on
synthetic data. Tests prove that it **fails** on a strategy reading the future, a leaking feature and a centred window.

---

## 13. Feature engineering

Definitions and warm-ups for every column are in `features/registry.py`. Windows count bars, so they span closures. Values are NaN until warm.

| Family | Features |
|---|---|
| Technical | Returns, log returns, momentum, volatility, TR, ATR, SMA, EMA, RSI, z-score, Bollinger, ADX and DI, Donchian (prior bars only), breakout flags, trend slope, volatility and ATR ratios |
| Structure (project definitions) | Fractal swings, BOS, CHoCH, liquidity sweeps, equal highs and lows, premium and discount, FVG, inverse FVG, day high and low, PDH, PDL, PDC |
| Microstructure | Spread, spread change and z-score, activity, volume ratio, quote imbalance, Amihud impact proxy, liquidity proxy, volatility clustering; tick rule, tick imbalance and tick rate on quotes |
| Regime | `regime` (`features/regime.py`): 1 trending up, 2 trending down, 3 ranging, 4 volatile, 0 none of these, NaN during the 218-bar warm-up. A trailing-data state label used to filter strategies, not a signal. Volatile wins any overlap |
| Macro and cross-market | Real yield, 10-year and 2-year yields, broad dollar, VIX and their changes; cross-market returns and rolling correlations; hours to FOMC. COT z-score |

SMC and ICT terms have no single standard. These rules are this project's.

---

## 14. Backtesting engine

`sim/engine.py` handles one event at a time. Before each execution event, every signal bar closed by its start is shown
to the strategy, so an order can only fill on a later event.

**Execution rules.** Longs buy at the ask and sell at the bid; shorts do the reverse. Stops and targets are tested against the side that would fill. A bar touching both counts as the stop. A gap through a stop fills at the open. Targets fill at the target, or better on a gap.

**Supported:** bars or ticks, market, limit and stop orders, expiry, brackets, modification, slippage, commission, swap, delay, extra spread, partial fills by volume participation, sizing, equity, trade journal, deterministic digest, a risk gate.

**Cost scenarios** (`sim/costs.py`). The observed spread is used as is. `spread_x2_equiv` adds $0.30 spread and $0.05 slippage. `stress` adds $0.60, $0.20 and $7 per lot commission. These are assumptions for stress testing, and are labelled. Swap defaults to zero.

**Metrics** (`sim/metrics.py`). Net, gross profit and loss, win rate, profit factor, expectancy, average win and loss, drawdown, Sharpe, Sortino, Calmar, recovery factor, average R, trade frequency, exposure, and breakdowns by strategy, regime, session, weekday, month, year and side.

---

## 15. Time Machine

The original Time Machine is **not a chart replay tool**. It is a Monte Carlo pre-trade simulator. So there are two components:

**Historical Replay** (`timemachine/replay.py`), built new from your specification: tick, S1 and bar replay, play, pause, resume, reset, speed, step, navigate, simulated orders and positions, event logs. Going back replays deterministically, so it cannot drift. Saved sessions record config, data fingerprint and digest, and `verify` re-runs and compares them. Charts can only show closed bars.

**Outcome Simulator** (`skills/outcome.py`), the original's method on gold. It reuses the upstream draw, verdict and percentile code. The untouched original reproduces its published TSLA result: 29.4 %, 25.5 %, 45.1 %, 33 fired, 13 target, 15 stop.

Adaptations: 288 slots on the New York day, local data, partial sessions, spread-adjusted R.
Inherited limit: bars are drawn independently, which removes volatility clustering, and gold's volatility clusters. Treat its odds as a reality check.

**Orchestration** (`timemachine/orchestrate.py`): experiments combining strategies, skill gates (FOMC, scanner RSI filter, wide spread), an ML veto and the risk engine; comparison tables; decision reconstruction by time; pre-trade Outcome Simulator; and look-ahead checks on the whole experiment. A single-strategy experiment reproduces that strategy's own trades exactly.

**Debug viewer** (`timemachine/viewer.py`): a dependency-free, read-only candlestick replay with controls, positions, entry and exit markers, stop and target levels, and event log. It was used on real 2024 data to check fills.

---

## 16. The eight strategy engines

All are in `strategies/engines.py`. Each has inputs, parameters, a grid, hypotheses, causal signals, shared execution, walk-forward, cost scenarios, reports and tests.

| Engine | Timeframe | Measurable rule | Regime handling | Main limitation |
|---|---|---|---|---|
| Mean Reversion | H1 | Fades z-score and RSI extremes to the 20-bar mean | Only with low ADX | ADX lags |
| Trend and Momentum | H1 | Channel breakouts with average alignment and ADX, only in the London and New York open hours, ATR in its 40th-80th trailing percentile, no late-Friday entries, 4-hour FOMC pause; 2 x ATR chandelier exit | Last completed day's trend; no entries in ranging or volatile regimes | Pays spread on breakouts; every added filter raises the backtest-luck risk |
| Statistical Arbitrage | H1 | Residual of a rolling log regression on silver or EURUSD | Correlation stability filter | **Only the gold leg trades**: relative-value timing, not a hedged spread |
| Behavioral | M15 | Buys down-shocks and low sweeps in a structural uptrend, sells up-shocks and high sweeps in a downtrend; skips spreads over 1.5 x the 60-bar median | Trend alignment; skips the volatile regime | "Panic" and "FOMO" name measurements, not emotions |
| ETF and Turnaround | H1 | Turn-of-month and first-notice windows; COT extremes tested | None | **ETF flows are not tested**; first notice is approximated |
| Seasonal | H1 | Weekday-hour cells chosen on training data only | None | 120 cells searched; CPI and payroll times unavailable |
| Overreaction and Gaps | H1 | Compares fading against following gaps and shocks | Session boundaries by closure | Weekend-open spreads are wide |
| London Open Momentum | M5 | Mon-Thu, 07:00-08:30 UTC: first bar moving 0.3 ATR from the Asian close, traded in that direction, held 2 bars, 0.5 ATR reversal exit, 1.0 ATR stop | Session window only | A few-minute hold: costs dominate; the hypothesis is a prior, not a finding |

Reports include performance by regime, session, weekday, month, year and side.

**Not yet evaluated:** the revised Trend and Behavioral rules and the London Open Momentum engine were added after the results in Section 2, which describe the original rules. Their tests prove the signals are causal and behave as specified, not that they have an edge. Run `python -m quantresearch.research.experiment trend behavioral london_open_momentum` on the warehouse and judge them by the deflated Sharpe.

---

## 17. Research methodology

Every report records the hypotheses, dataset version, period, features, parameters, method, results, significance and limitations, and goes into `research/experiments.sqlite`.

| Control | How |
|---|---|
| Overfitting | Chronological walk-forward, with a final holdout |
| Selection bias | Deflated Sharpe counts every variant tried |
| Multiple testing | Benjamini-Hochberg across each hypothesis family |
| Overlapping data | Newey-West errors and block bootstrap |
| Market drift | Tests use excess over the unconditional mean; reports show gold's buy-and-hold return |
| Cost realism | Observed spreads, and base, doubled and stressed scenarios |
| Parameter fragility | A sensitivity table across the grid |
| Sequence luck | Monte Carlo reshuffling of trades |

**Verdict labels:** `survives_tests_needs_paper_trading` needs positive OOS results, deflated Sharpe above 0.95, a positive holdout under observed and stressed costs, and (added 2026-11-28) beating a risk-matched buy-and-hold of gold in both the OOS folds and the holdout.

**Risk-matched buy-and-hold (added requirement, 2026-11-28).** Gold rose about 6.7x over the out-of-sample years, so long-biased rules got a free tailwind. Every report (engines and catalogue) now compares the strategy's daily returns with a long-only gold position scaled to the strategy's own volatility over the same days, separately for the OOS folds and the holdout (`risk_matched_benchmark`). At equal risk this is a Sharpe-against-Sharpe comparison. A strategy that does not beat it in both periods cannot be labelled promising, even if it passes everything else. **No engine reached it.** Even that label never approves live trading.

**Strategy catalogue** (`strategies/catalogue/`, `research/catalogue.py`, `research/trials.py`): documented gold strategies with fixed parameters, run through exactly this methodology. The full list with sources and exact rules is in [docs/strategy_catalogue.md](docs/strategy_catalogue.md). Hard rules, enforced in code: each strategy has one parameter set, locked at its first run (a change under the same name is refused); every run goes into the ledger and every variant into the trial book, and nothing is deleted (a test fails if a catalogued name disappears); the deflated Sharpe that decides "promising" counts every variant ever tested by the engines and the catalogue in N, with the spread of luck taken from the catalogue's out-of-sample Sharpes (the engines' in-sample per-variant training Sharpes are too noisy for that and pushed the bar to an unreachable ~4.2 annualised Sharpe), and `--rescore` re-judges every strategy against the final count so early and late runs face the same bar. **This deflation method was changed once, after the batch 1 results were seen** (the first version took the spread from the engines' in-sample training Sharpes too, which set an unreachable bar; no batch 1 strategy passed under either version). **It is now frozen**, together with the "promising" criteria below, for this catalogue run, whatever the results; "promising" needs 30+ OOS trades, positive OOS, positive holdout at observed and stressed costs, that project-wide deflated Sharpe above 0.95, and (added 2026-11-28, applied before the final rescore) beating risk-matched buy-and-hold in both the OOS folds and the holdout. Run with `python -m quantresearch.research.catalogue --all` (or `--batch N`, or names), or from the dashboard's Strategy Catalogue page, whose "Run all queued" works through a queue in a background child process and survives restarts. Catalogue strategies are research candidates only; they never feed the Overview's live signals.

**Laboratory tools** (`research/stats.py`, `research/lab.py`): distributions, ADF and KPSS, cointegration with half-life, rolling correlation, HAC regression, bootstrap, probabilistic and deflated Sharpe, Monte Carlo, clustering, regime detection, CUSUM change points, anomaly detection, mutual information, and permutation importance.

---

## 18. The eleven trading skills

**Sources verified.** Both are by Jake Schincariol, MIT licence, and kept unmodified with their licences in `skills/upstream/`:
`trader-skill` at `5189e79`, and `day-trader-skill` at `3d0f4ef`. Their own test suites pass on this Python: **315 and 175 tests**.

All are instruction files plus standard-library Python. Six skills exist in both repositories; per your decision the day-trader versions are used. Pelosi, Tax and Rebalancer are not in your list and are not integrated.

| Skill | Source | Kept | Adapted for XAUUSD | Not available |
|---|---|---|---|---|
| Trader News | v2 | Tags, seen-once, headlines are leads | Gold relevance, GDELT, gold watchlist | 8-K logic for spot gold; Yahoo RSS for gold tickers unverified |
| Trader Risk | v2 | Exits never blocked; fail-closed checks | Runs on the independent risk engine, with lots and added limits | US-ticker check; PDT |
| Trader Budget | v2 | Smallest of limits; says which decided; refuses | Lots, contract size, leverage | Alpaca format |
| Trader Broker | v2 | One yes per order; "sure" does not count | MT5; approval is real code | Alpaca and IBKR connectors |
| Trader Alerts | v2 | Upstream's engine called unchanged | Gold clock, short side, local quotes | NYSE tables |
| Trader Analyst | v2 | Original tool run unchanged | Miners as context | **Gold has no earnings or filings** |
| Trader Screener | v1 | Original engine | Any metrics table | Stock-picking purpose |
| Trader Copycat | v1 | Original parser and diff | Gold ETF and miner filter; filing date as availability | **13Fs do not cover spot gold** |
| Day Trader Scanner | v2 | `find_setups` called unchanged | Timeframes replace the stock universe | About 5,900-stock universe |
| Entry Timer | v2 | Closed-bar trigger, volume check, expiry | Short side; session choice | NYSE clock |
| Exit Planner | v2 | Stop rules, rounding, refusals | Short side; spread-aware refusal | NYSE flat-by |

**Fidelity proven by test.** On upstream's real TSLA data, the Entry Timer gives 354.47 to 362.18, waiting at 10:12, triggered at 364.18 at 10:15, and 365.75 at 10:35 with the volume check. The Exit Planner matches upstream's `compute` across all rules (358.32, 370.04). The Budget Manager in stock terms gives upstream's 8 shares and $46.88.

Every skill is callable from the API and dashboard, with instructions, status and a run log. Testing used fixtures and offline calls. **Live SEC, Yahoo and news calls through skills were not exercised**, apart from GDELT and the `--help` runs of the tools.

---

## 19. Machine learning

`ml/pipeline.py` provides triple-barrier labels with their end times, purged expanding-window folds with an embargo, permutation feature selection on a later slice, a small hyperparameter grid (logistic, random forest, gradient boosting), isotonic calibration on later data, versioned artifacts with checksums, and PSI drift reports. Inference gives a probability and confidence, and abstains on missing inputs, inputs outside the training domain, or low confidence.

**Promotion gate:** at least 0.001 mean log-loss gain over a constant, **and** wins in at least 75 % of folds. An earlier, weaker gate promoted a model that gained 0.0002 and was right 46.7 % of the time when it acted on the holdout. That was found, fixed, and its record retained as not promoted.

`evaluate_strategy_conditions` asks when a strategy works, using trades as labels. Regimes use a Gaussian mixture, ordered by volatility.

**Training:** `python -m quantresearch.ml.train_cli --tf h1 --end 2024-10-30`. Retraining is the same command; a new model is promoted only if it passes the gate and beats the current one.

ML cannot start a trade. It can only veto or be ignored, and tests show the ML module cannot reach a broker.

---

## 20. Knowledge and research layer

`knowledge/store.py` is SQLite FTS5. Every document has a kind: historical fact, live observation, statistical finding, model prediction, research hypothesis, unverified assumption, or documentation. Results carry source, time and kind. `as_of` hides later or undated documents, so it is safe in replays.

Indexed automatically: this README, all skill instructions, strategy documents and hypotheses, research reports, model cards and headlines. Add your own literature to `data/research/library/<kind>/*.txt|md`, then use Rebuild index. No literature corpus is included, since copyright is yours to manage.

---

## 21. Decision engine

`decision/engine.py` takes strategy votes, skill-gate context and an optional ML view, and returns a proposal with reasons, or none. It has no route to a broker. Conflicts stand down or use a majority, with a configurable minimum number of votes. ML can be off, a veto, or required. Each position keeps the exit logic of the strategies that opened it. `explain(time)` reconstructs any decision.

---

## 22. Risk engine

`risk/engine.py` uses only the standard library. **A test proves it imports nothing from strategy, ML, skill, knowledge or decision code**, so their failure cannot affect it.

| Check | Default |
|---|---|
| Risk per trade (reduces size) | 0.5 % of equity |
| Daily loss (lockout for 24 hours) | 2 % |
| Drawdown (lockout until a person clears it) | 10 % |
| Maximum order, total exposure | 2 and 5 lots |
| Concurrent positions, entries a day | 3 and 10 |
| Stop required, maximum stop distance | Yes, 5 % |
| Minimum reward to risk | 1.0 |
| Spread, slippage from planned entry | $1.50, $1.00 |
| ATR as a share of price | 3 % |
| FOMC restriction window | 15 minutes either side |
| Emergency stop | Blocks entries; can close all |

Rules: exits that only reduce risk are always allowed, even during an emergency stop. Missing data rejects the order, including a failed event calendar. Size can only be reduced. Lockouts and emergency stops survive restarts. Every decision is stored.

**Defaults are starting points. Set them for your own account.** The $1.50 spread limit may need review in fast markets given the 2025 and 2026 spreads.

---

## 23. MT5 integration and trading modes

**Runtime requirements:** Windows, the `MetaTrader5` package (5.0.6231, installed), and an installed terminal it can attach to. MetaQuotes user folders exist on this PC, but no terminal was running, and I did not launch it.

**`execution/mt5_adapter.py` covers:** connection, reconnection, account, balance, equity, positions, pending orders, deal history, quotes, symbol specs, spread, market and pending orders, modification, closure, stop and target management, return-code handling and audit logging.

**Tested against a mock, never a real terminal or broker.** Safety behaviour is proven by test: orders are off by default, real-money accounts are refused without activation, requotes retry, and an order with an unknown outcome is never resent.

| Mode | Sends orders? |
|---|---|
| Research, Simulation | No |
| Paper | To the paper broker only |
| Assisted | Needs a typed yes for one exact order; risk rechecks after the yes |
| Automated | No person; on a real broker it requires live activation |

**Live activation requires all of:** `QR_LIVE_ENABLED=1`, `QR_BROKER=mt5`, the exact phrase `ENABLE LIVE TRADING AT MY OWN RISK` in this process, and a broker that allows trading. It is never saved, so a restart always falls back.

**Connecting a demo account, step by step (Windows):**

1. **Install the MT5 terminal** from your broker's website, open it, and log in to a **demo** account once (File > Open an Account, or use the login the broker emailed you). Leave the terminal running.
2. **Install the Python package:** `python -m pip install MetaTrader5` (Windows only).
3. **Set the environment variables** in the same PowerShell window you will run Python from. The login and server are on the broker's demo-account email:
   ```powershell
   $env:QR_BROKER = "mt5"
   $env:QR_MT5_LOGIN = "<your demo login number>"
   $env:QR_MT5_SERVER = "<your broker's demo server name>"
   Set-Item -Path Env:QR_MT5_PASSWORD -Value (Read-Host "MT5 demo password" -MaskInput)
   ```
   These last for that window only. For something durable, store them once in Windows Credential Manager instead; the platform reads that first.
4. **Run the read-only check:** `python -m quantresearch.execution.mt5_check` (from the repository root, with `PYTHONPATH=src`). It prints the account (login, server, demo or real, balance, leverage), a live XAUUSD quote with the spread in points and dollars, and the last five closed H1 bars, then disconnects. It cannot place orders. If your broker spells gold differently, add `--symbol XAUUSDm` (or `GOLD`); when a symbol is missing it lists the first ten the terminal has.
5. **Only if it ends with `PASS`**, start the API (`PYTHONPATH=src python -m quantresearch.api.server`) and look at `/api/health`: the `mt5` block should read `connected`, `account_type` `demo`, with your balance and a live bid and ask. Then move on to paper trading. A `FAIL` explains what to fix; do not go further until it passes.

The health block's `status` is `not_configured` when `QR_BROKER` is not `mt5` (the paper account), `disconnected` when the terminal cannot be reached, `connected` when it can, and `mock` only for the test stand-in. The check script reads the terminal and nothing else; real prices from it are not yet used by the paper broker.

**Forward test 001 (demo only).** The seven catalogue near-misses run as one pre-registered portfolio on the MT5 demo
account: `docs/forward_test_001.md` holds the rules and the single pass/fail, and `src/quantresearch/forward/` the code.
Orders go through the order router and a separate risk engine instance (state in `data/app/forward_001_risk.sqlite`,
audit in `forward_001_audit.sqlite`, journal in `forward_001.sqlite`). The broker it uses, `DemoOnlyMt5Broker`, reads
`account_info().trade_mode` before every order, close, modify and cancel and refuses all of them unless MT5 reports
`ACCOUNT_TRADE_MODE_DEMO`. Anything else, including a contest account or a failed reading, writes
`forward_test_real_account_refused` to the audit log, pauses the test and shows a red alert on the Forward Test page
until a person clears it. `QR_LIVE_ENABLED` plays no part and the live path is unchanged: the dashboard's own MT5
broker still needs live activation. Start it once with the page's Start button or
`python -m quantresearch.forward.runner start`. The start is refused unless the pre-registration is committed and
unchanged, the parameters match their lock, the account is a hedging demo, and equity is at least $120,000. Once
started, the API's `forward-test` thread ticks every 15 s and resumes after restarts. Bars that closed while the
PC was off are processed on return: their entries are logged as missed (late) and their exits are executed.
The prediction (the same decision code replayed over the period's M1 bars, observed costs) is recomputed hourly.

**Suggested path:** run read-only with `QR_BROKER=mt5` against a **demo** account, compare spreads and swap, then paper trade, then assisted on demo. Only much later consider real money, and only for a strategy that has passed.

---

## 24. API

All on `http://127.0.0.1:8000`.

| Area | Endpoints |
|---|---|
| Status | `GET /api/health`, `/api/overview`, `/api/coverage`, `/api/jobs/{id}` |
| Charts | `GET /api/bars?tf&limit&end` |
| Intelligence | `GET /api/intel/calendar`, `/macro`, `/cross`, `/news`; `POST /api/intel/news/refresh` |
| Research | `GET /api/research/strategies`, `/report/{name}`, `/experiments`, `/analysis`, `/search`; `POST /api/research/run/{name}`, `/reindex` |
| ML | `GET /api/ml/models`, `/inference`; `POST /api/ml/train`, `/promote/{id}` |
| Replay | `POST /api/replay`, `/api/replay/{id}/control`, `/api/outcome`; `GET /api/replay/{id}`, `/report`; `WS /ws/replay/{id}` |
| Skills | `GET /api/skills`, `/api/skills/{key}`, `/api/skills/logs/all`; `POST /api/skills/{key}/run` |
| Terminal | `GET /api/terminal`; `POST /api/terminal/order`, `/approve`, `/decline/{fp}`, `/close/{ticket}`, `/mode/{mode}`, `/activate-live`, `/deactivate-live` |
| Risk | `GET /api/risk`; `PUT /api/risk/limits`; `POST /api/risk/size`, `/emergency-stop`, `/emergency-clear`, `/clear-lockout` |
| Audit | `GET /api/performance`, `/api/audit` |
| Discovery (read-only) | `GET /api/discovery/runs`, `/status`, `/candidates?group=`, `/candidate/{cid}`, `/distribution`, `/report` |

---

## 25. Dashboard

Next.js 15, React 19, TypeScript and Lightweight Charts, exported statically and served by FastAPI. It has dark styling and works down to phone width.

| Section | Shows |
|---|---|
| Overview | Price (labelled if not live), account, mode, session, regime, health, risk status, recent signals |
| Market Charts | Timeframes, averages, Bollinger, BOS and CHoCH, sweeps, FVG and iFVG, session and swing levels, drawing tools |
| Market Intelligence | Macro, COT, cross-market, FOMC calendar, news |
| Quantitative Research | Engines, hypotheses, folds, sensitivity, statistics, search, ledger |
| Strategy Discovery | Run status, runtime, experiments, hypotheses, rejections, families, survivors per stage beside the null market, out-of-sample distribution, folds, search methods, CPU and memory, checkpoints, qualification, and full evidence for each candidate |
| Machine Learning | Registry, probabilities, abstention, regime, drift, training |
| Time Machine | Experiment builder, replay controls, streaming, decisions, results, Outcome Simulator |
| Trading Skills | All eleven, fidelity records, runs, logs, original instructions |
| Trading Terminal | Connection, account, modes, orders, approvals, positions, history |
| Risk Management | Limits, exposure, emergency stop, lockouts, sizing, decisions |
| Performance and Audit | Equity, comparison, journal, model decisions, audit log |

Controls call real services, and there are no hardcoded statistics. Empty states say what is missing.

---

## 26. Configuration and environment variables

| Variable | Default | Purpose |
|---|---|---|
| `QR_DATA_ROOT` | `data/` | Where all data lives |
| `QR_PORT` | 8000 | API port |
| `QR_BROKER` | paper | Set to `mt5` to attach MT5 |
| `QR_LIVE_ENABLED` | off | Must be `1` before live activation is possible |
| `QR_CORS_ORIGINS` | none | Exact extra browser origins allowed to call the API, comma-separated (the hosted dashboard). Wildcards are refused |
| `QR_VERIFY_CF_ACCESS` | off | `true` makes the API check the Cloudflare Access token (`Cf-Access-Jwt-Assertion`) on every HTTP and WebSocket request and answer 403 without a valid one. Needs the next two; the server refuses to start without them. Local access to `127.0.0.1` has no token, so it is refused while this is on |
| `QR_CF_ACCESS_AUD` | none | The Application Audience (AUD) tag of the API's Access application (Zero Trust > Access > Applications > your app > Overview) |
| `QR_CF_ACCESS_TEAM` | none | Your Access team name, as in `https://<team>.cloudflareaccess.com`. The signing keys are fetched from it at startup and refreshed every 24 hours |
| `NEXT_PUBLIC_API_URL` | none | Build-time, for the dashboard: the API address for a hosted copy. Compiled into the JavaScript, so the dashboard must be behind Cloudflare Access. Never commit it |
| `QR_MT5_LOGIN`, `QR_MT5_PASSWORD`, `QR_MT5_SERVER` | none | Session-only fallback. Prefer the credential store |
| `QR_MT5_UTC_OFFSET_HOURS` | 0 | Hours the MT5 server clock is ahead of UTC (Exness: 0). Used to stamp broker bars in UTC |
| `QR_FORWARD_TEST` | 1 | Runs forward test 001's worker in the API once the test has been started. `0` turns the worker off (missed bars are caught up when it is back on) |
| `QR_FORWARD_SYMBOL` | `QR_MT5_SYMBOL` or XAUUSDm | The forward test's MT5 symbol |
| `QR_WAREHOUSE_SYNC` | 1 | With MT5 attached, the API brings the warehouse up to date once per UTC day. `0` turns that off |
| `SEC_USER_AGENT` | none | Your name and email, for SEC-based skills |

Risk limits are saved to `data/app/risk_limits.json` from the dashboard. Parameters and grids are in `strategies/engines.py`.

---

## 27. Dependencies

Direct versions (`requirements-lock.txt` has everything):

| Python | Version | | Python | Version |
|---|---|---|---|---|
| pandas | 3.1.0 | | fastapi | 0.162.0 |
| numpy | 2.5.4 | | uvicorn | 0.54.0 |
| pyarrow | 26.0.0 | | keyring | 25.7.0 |
| duckdb | 2.0.0 | | MetaTrader5 | 5.0.6231 |
| scipy | 1.18.1 | | pytest | 9.2.0 |
| statsmodels | 0.15.0 | | httpx | 0.28.1 |
| scikit-learn | 1.9.1 | | tzdata | 2026.6 |
| joblib | 1.6.0 | | | |

Node: `next` 15.5.28, `react` 19.2.0, `lightweight-charts` 4.2.3, `typescript` 5.9.3, `playwright` 1.56.1, and `dukascopy-node` 1.51.1 (pinned, fetched by `npx`). Next was moved off 15.5.6 because of a published security advisory.

---

## 28. Commands

Prefix Python commands with `PYTHONPATH=src`.

```bash
# Data
python -m quantresearch.warehouse.fetch_months m1 --workers 2        # resumes; refreshes the current month
python -m quantresearch.warehouse.fetch_months tick --workers 2 --start 2010-09 --min-free-gb 10
python -m quantresearch.warehouse.build --start 2003-05              # rebuild bars
python -m quantresearch.warehouse.audit                              # completeness report
python -m quantresearch.external.store                               # refresh external data

# Research
python -m quantresearch.research.experiment                          # all eight, or name some
python -m quantresearch.ml.train_cli --tf h1 --end 2024-10-30
python -m quantresearch.timemachine.lookahead --tf m15 --start 2024-01-01 --end 2024-07-01 "2024-03-29 00:00"

# Tools
python -m quantresearch.timemachine.viewer --tf m15 --start 2024-03-01 --end 2024-05-01   # http://127.0.0.1:8765
python -m quantresearch.api.server                                                         # http://127.0.0.1:8000

# Strategy discovery (research only)
python -m quantresearch.discovery.store --dataset real                        # feature store, pre-holdout data only
python -m quantresearch.discovery.controller supervise --run-id main --hours 48 --workers 3
python -m quantresearch.discovery.controller status --run-id main
python -m quantresearch.discovery.controller stop --run-id main              # finishes the round, checkpoints
python -m quantresearch.discovery.controller run --run-id main --resume      # continue after a stop or crash
python -m quantresearch.discovery.controller report --run-id main
python -m quantresearch.execution.discovery_paper --run-id main              # forward paper trading, registered candidates only

# Tests
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests
cd frontend && npx tsc --noEmit && npx next build
python tests/e2e_server.py 8123 &     # throwaway synthetic data; cannot touch your paper account
cd frontend && node e2e/run.mjs http://127.0.0.1:8123
```

---

## 29. Tests and actual results

| Suite | Result | Date |
|---|---|---|
| Project tests (`pytest tests`) | **434 passed** (405 existing + 29 discovery) | 2026-11-02 |
| Discovery tests after the pre-run fixes (`pytest tests/test_discovery.py`) | **30 passed** | 2026-11-02 |
| Browser workflows (`e2e/run.mjs`) | **34 of 34 passed** | 2026-11-02 |
| TypeScript check, production build | Passed | 2026-11-02 |
| Upstream `day-trader-skill` suite | 315 passed | 2026-11-02 |
| Upstream `trader-skill` suite | 175 passed | 2026-11-02 |
| Look-ahead on real data | 15 of 15 cutoffs passed | 2026-11-02 |
| Warehouse audit on real data | Passed | 2026-11-02 |

**Covered:** data pipeline, corrupted files, interrupted builds, time zones and DST, session boundaries, duplicates, retries and resume, each risk limit passing and blocking, emergency stop, restart persistence, MT5 mock paths including reconnection, API validation, approval rules, and secret redaction.

**Browser checks:** all ten sections, chart and drawing tool, risk rejection, paper order, emergency stop, assisted yes rules, replay step, play, pause and results, skills, search, phone width, no browser errors.

**Not tested:** a real MT5 terminal, any real broker, live trading, real-time feeds, SEC calls without an identity, and soak testing over days.

---

## 30. Bugs found and fixed

| Bug | How it was found | Fix |
|---|---|---|
| Last day of every month lost | Phase 0 audit | End date, verification, full re-download |
| Time units compared as raw integers: 289 million "hours to FOMC", and risk to the engine clock | Real-data check | All clock comparisons in nanoseconds, with regression tests |
| Weak ML gate promoted noise | Checked the model on the holdout | Material gain plus fold consistency required |
| Decision engine ignored strategy exits, making seasonal look +$18,938 instead of −$3,762 | Comparison disagreed with the report | Keeps voter exits; test requires identical trades |
| Weekend bars dropping out of turn-of-month windows | Test | Non-trading days keep the last trading day's position |
| Missing Friday closure for a Saturday Christmas | New audit on real data | Calendar rule |
| Quote exactly at the cutoff mishandled by the look-ahead harness | Test after the time fix | Quotes at T count as known at T |
| Replay log buried by bar markers; "100 %" regime confidence; session label while closed | Reading screenshots | Filtered; shown as model fit, fitted on earlier bars; labelled closed |
| News query rejected by GDELT | Real-data check | Corrected query and readable errors |
| `consolidate.py` could corrupt the old archive | Phase 0 | Refuses that folder, reports duplicates |
| Next.js security advisory | npm warning | Upgraded |
| A limit or stop entry filled inside a bar could also take its target on that bar, though the bar cannot show which came first. One-line rules scored an out-of-sample Sharpe above 5 | A discovery smoke run produced impossible results | Opt-in `EngineConfig(intrabar_entry_target=False)`, used by discovery; the kernel matches. The seven engines use market orders and were not affected. The smoke run is archived, not deleted, and its ledger row is marked superseded |
| `ProcessPoolExecutor(max_tasks_per_child=...)` deadlocked under spawn when a worker retired | Smoke run hung with no live workers | The whole worker pool is recycled every 12 rounds instead |

---

## 31. External services and credentials

| Service | Credential | Note |
|---|---|---|
| Dukascopy, via `dukascopy-node` and direct files | None | Free. Check its terms for your use |
| FRED CSV, Federal Reserve, CFTC | None | Public |
| GDELT | None | One request per 5 seconds |
| SEC EDGAR | Your name and email | Only for Analyst, Copycat and 8-K news |
| Yahoo, Stooq, Nasdaq Trader | None | Used by original tools only. Yahoo's endpoints are unofficial |
| MT5 broker | Yours, stored locally | Only if you connect it |

No external AI service is called by this platform.

---

## 32. Security

- The API binds to `127.0.0.1` and has no authentication, so **do not expose it**. A test checks the bind address. The optional Cloudflare Tunnel in Section 1 is the one deliberate exception: it publishes the API, so Cloudflare Access must sit in front of both the tunnel and the dashboard, and the address must never be shared.
- Credentials come from Windows Credential Manager through `keyring`, or from session variables. They are never in config files, the frontend or logs, and credential objects hide themselves when printed.
- Anything logged passes through a redactor. A test scans tracked files for passwords, and another checks that API responses contain none.
- The audit log has a hash chain, so an edit is detectable. The dashboard shows whether it is intact.
- The chain detects edits but does not prevent someone with file access from rewriting all of it.

---

## 33. Operational workflows

**Monthly data refresh:** rerun `fetch_months m1`, `build`, `audit`, and `external.store`. If the audit fails, investigate before using the data.

**Daily top-up from MT5:** `python -m quantresearch.warehouse.mt5_sync` tries Dukascopy for the current month, then copies the MT5 terminal's M1 bars for anything still missing (archived under `warehouse/broker/mt5/m1`), rebuilds the affected months and writes `reports/mt5_sync.json`. Broker rows only fill days Dukascopy lacks and minutes after its last bar, carry flag 16, and are replaced when Dukascopy later covers them. The API runs the same sync once per UTC day while MT5 is attached (`QR_WAREHOUSE_SYNC=0` turns it off). To run it without the API, schedule the command in Windows Task Scheduler.

**Cross-market series from MT5:** when Dukascopy refuses (a plain 403 from some IPs), `python -m quantresearch.external.store` falls back to the MT5 terminal for each cross-market series: XAGUSD, EURUSD, GBPUSD, USDJPY, DXY (dollar index), US500 and USOIL, with the account's suffix taken from `QR_MT5_SYMBOL` (`XAUUSDm` gives `XAGUSDm`). Exness has no Treasury bond CFD, so `ustbondtrusd` stays Dukascopy-only. The raw copies live in `external/cross/provider/` and `external/cross/mt5/`. The merged file readers use keeps every Dukascopy bar, takes MT5 bars only for days Dukascopy lacks and hours after its last bar, and marks them `flags` 16 with `source` `mt5:<symbol>`. Only closed bars are stored. MT5 serves what the terminal holds (on this account, H1 back to mid-2018 for silver and FX, 2019 for DXY, mid-2023 for US500 and USOIL), and archived bars are kept, so the history grows with each refresh. `--mt5-only` skips Dukascopy, `--no-mt5` disables the fallback, and the daily MT5 sync tops these series up from MT5 alone.

**Researching a change:** edit the engine, run its tests and the look-ahead test, run `research.experiment <name>`. Treat the holdout as spent; a truly unseen test now needs new data as time passes.

**Replay:** Time Machine page, choose period and components, Load replay, then step or play. Use Results and decision reconstruction.

**Paper trading:** default mode. Without MT5 prices come from stored history, so this tests the path, not live conditions.

**Emergency:** Risk Management, then EMERGENCY STOP. It blocks entries and closes positions, and stays on after a restart until cleared.

---

## 34. Recovery and troubleshooting

| Problem | Do this |
|---|---|
| Download interrupted | Rerun the same command. It resumes |
| "database is locked" | Another fetch is running. Only run one per kind at a time |
| Tick job stopped | Rerun it. Check free disk; it stops under the limit |
| Direct feed returns 503 | Use `fetch_months`; the provider's file feed comes and goes |
| Audit fails on a date | See whether it is a real closure, then add a calendar rule, or re-fetch that month |
| Build interrupted | Rerun. Writes are atomic |
| Dashboard shows "not built" | Run `build` |
| Dashboard pages missing | Run `npx next build`, then restart the server |
| Trading locked out | Risk Management shows why. A drawdown lockout needs a person to clear it |
| Risk blocks every order | Often the news window, a stale price or missing ATR. The reasons are listed |
| Audit chain broken | A database row was changed. Investigate before trusting history |
| Replay refuses ML | The period is inside training. Use a later period or an earlier-trained model |
| Reset paper account | Stop the server and delete `data/app/paper.sqlite` |

Backups: `data/raw/` and `data/warehouse/csv`, `ticks`, `bi5` are the sources. Everything else can be rebuilt, except application history in `data/app/` and `data/research/`.

---

## 35. Project rules

1. Preserve working code and never destroy datasets. Keep a verified copy before replacing anything.
2. Never fabricate data or results, and never weaken validation to hide a problem.
3. No fake functionality.
4. Do not push or deploy without explicit instruction. Local commits only, with no AI attribution.
5. This README is the only project Markdown file. Original skill definitions keep their own format.
6. Never claim a test passed unless it ran. Never claim live MT5 was tested without a real terminal.
7. Negative research results are valid and are reported.

---

## 36. History

- **2026-11-02** Autonomous strategy discovery added (Section 37), tested, and a 48-hour discovery run launched.
- **2026-11-02** All phases implemented. Full M1 re-download repaired the month-end defect. Research and ML run on real data, with results above. The owner waived per-phase approval, so phases ran in one working session instead of a fresh one each. Commits are local and have not been pushed.
- **2026-11-01** Phase 0 audit found the month-end defect, ask data in ticks, and the true nature of the original Time Machine.
- **2026-10-30 to 31** First-generation pipeline, 2,420 Parquet files.
- **2026-10-29** Candle schema, CSV ingestion, session audit.

---

## 37. Autonomous strategy discovery

A research laboratory that generates its own hypotheses for XAUUSD, turns them into complete executable strategies,
and tries hard to disprove each one. It is research only: the `discovery` package cannot import the execution, risk,
API or broker code (a test checks the import graph), and the only way out of it is registration for **paper**
trading. Zero qualified strategies is an acceptable result. The gates are fixed in code before any result is seen.

### Validation design

| Period | Use |
|---|---|
| 2003-05-05 to 2024-10-30 | Research data. The discovery feature store is built from this alone, so workers cannot read later bars |
| 2024-10-30 to 2026-10-30 | **Contaminated holdout.** Examined during earlier development and defect fixes. Never used for selection. A frozen, already-qualified candidate may have one recorded confirmatory look, which can only warn, never upgrade |
| After registration | Forward paper trading: the only genuinely unseen data |

Inside the research data, candidates are produced at eight **vintage cutoffs** (2009-01-01, then every two years to
2023-01-01). For each cutoff the search sees only the six years before it: fitness on the first four (inner train),
selection on the last two (inner validation). Selected candidates are then **frozen** and tested only on the period
from the cutoff to 2024-10-30, which their search never saw. Every calendar year is recorded as its own fold. The
search scheduler is rewarded by training-window outcomes only, and seeds can come only from the same or earlier
vintages, so out-of-sample results never shape a candidate.

### Hypothesis generation

| Method | What it does |
|---|---|
| Genetic programming | Populations of typed rule trees; tournament selection, crossover, mutation of thresholds, operators, features, windows and exit genes; immigrants, elitism, a complexity penalty, archives carried across rounds |
| Genetic feature selection | Each task draws a feature subset; weights rise for features in candidates that pass training-window selection |
| Bayesian optimisation | Tree-structured Parzen search over 17 market-behaviour templates: opening ranges, session sweeps, previous-day levels, squeezes, pullbacks, exhaustion, gaps, silver and dollar moves, real yields, FOMC, payrolls, momentum, session transitions, structure breaks, liquidity sweeps, calendar cells, turn of month. Templates are seeds, not limits |
| Conditional-probability miner | Decile and pairwise cells of forward returns on training data, overlap-adjusted t, Benjamini-Hochberg within the task, mutual-information ranking. Strong cells become rules |
| Symbolic regression | Evolves numeric expressions whose rank correlates with forward returns in training. Thresholds at training quantiles become rules |
| Random grammar | An unguided control, to show what search adds over chance |
| Proposals | Externally supplied specs (a person, or a language model) pass the identical pipeline. No paid API is used |

Feature leaves (119 to 167 per timeframe) are scale-free and causal: momentum, trend, oscillators, volatility and
squeezes, bar shape, structure and sweeps, previous day and week levels, Asian, London and New York opening ranges,
gaps and shocks, spreads and activity, calendar and sessions, higher-timeframe states from closed bars only,
silver, euro, yen, dollar-index and Treasury-bond moves and divergences, the gold/silver ratio, real yields, the
curve, breakevens, VIX, FOMC and approximate payroll timing, and COT positioning. **CPI, PCE, payrolls,
unemployment, GDP and the broad dollar index are excluded**, because only revised values are available. The
validator refuses them.

### Strategy format

Every candidate is a JSON document (`discovery/spec.py`): timeframe, direction (long, short or both), long and short
conditions, regime filter, no-trade conditions, sessions, entry (market, stop or limit with offset and expiry), stop
(ATR or swing), target (none, R multiple or ATR), exits (time, condition, trailing, breakeven, trading-day end, flip),
guards (spread, FOMC) and sizing. Conditions use whitelisted, backward-looking operators only. Nothing is ever
executed as code. Canonical hashing removes duplicates, and identical trade sequences are flagged as equivalent.

### Execution

A compiled kernel (`discovery/kernel.py`, numba) applies the event engine's rules exactly. A parity test checks it
trade by trade against `sim.engine.Engine` under several cost cases and every entry and exit type. The difference is
below 1e-9 per ounce. Costs are the observed bid and ask, plus scenarios with a wider spread, slippage, commission,
a one-bar delay and a trade-through rule for limits. **Financing is charged** in every scenario except one labelled
`no_swap`, from an assumed model: a long pays fed funds + 2.5 %, a short pays max(0, 2.5 % - fed funds), per night.
A limit or stop entry filled inside a bar may be stopped on that bar but may not reach its target on it.

### Stages and gates

| Stage | Gate (thresholds in `validate.GATES`) |
|---|---|
| 1 Screen (inner train) | Valid, at least 40 trades, positive expectancy after costs, exposure under 90 %, no single trade over half the profit |
| Selection (inner validation) | At least 20 trades, positive expectancy and Sharpe; diverse picks, at most 3 per task, at most 2 per structural family per timeframe and vintage |
| 2 Walk-forward (frozen) | At least 50 trades, positive expectancy, at least half the years positive, first two years positive |
| 3 Statistics | Benjamini-Hochberg over **every** out-of-sample look at q 0.05 (Newey-West t); deflated Sharpe at least 0.95 with the look count as trials; PBO at most 0.5 by CSCV; Sharpe above the 95th percentile of null-market candidates; block-bootstrap Sharpe interval above zero |
| 4 Robustness | Positive under a wider spread, stress costs and delay; +-15 % perturbation of every parameter (70 % still positive, median Sharpe at least half); volatility and trend regimes; both sides independently; at least 60 % of years positive and no year over half the profit; feature-dependency report |
| 5 Adversarial | Survives removing the top 5 % of trades; alpha beyond gold's daily drift (Newey-West); works after six months of falling gold; beats random entries with the same exits and signal counts (p < 0.05); timing placebo reported |
| 6 Confirmation | Authoritative event engine on signal bars, and on M1 execution bars year by year |

Passing stage 6 means **qualified for controlled paper trading only**. As more candidates are looked at, the
statistical bar rises, and a candidate can lose a status it held earlier.

**Null-market calibration.** The identical pipeline also runs on sign-randomised markets. Every bar keeps its real
size, wicks, spread, activity and timing, but each move's direction is a coin flip, so no rule can have an edge.
How often candidates survive there measures the pipeline's own false-positive rate, and it feeds the null gate.

**Ranking.** Stage first, always. Within a stage, a score combines out-of-sample expectancy, confidence, deflated
Sharpe, drawdown, sample size, fold consistency, parameter stability and cost sensitivity, with penalties for regime
concentration, complexity, single-feature dependence, correlation with the existing engines (benchmark table: `benchmarks.parquet` in the run folder; a stale-cache warning appears if it predates an engine), one-sided
performance, and timing that does not matter. A large return cannot compensate for a failed gate.

### Controller

Synchronous rounds of parallel tasks, applied in task order, with seeds derived from the run seed, round and task, so
a run gives the same results on any worker count. Each round's results commit in one transaction together with a
verified checkpoint, so a crash loses only the round in progress, which is recomputed identically on resume. Tests
check identical results for one and two workers, after a clean interruption, and after a hard kill mid-round.
Safeguards: a memory guard that pauses work or sheds workers, below-normal priority, pool recycling, task timeouts,
sleep prevention, Ctrl+C or a `STOP` file to finish the round and stop, a supervisor that resumes after a crash, a
48-hour default limit, retirement of exhausted search cells (novelty below 2 % for four tasks with no selections),
and a futility stop after 24 hours if nothing is statistically validated and nothing has passed walk-forward for 40
rounds.

### Where things are

| Item | Location |
|---|---|
| Feature stores (real and null) | `data/research/discovery/store/` |
| Every experiment, candidate definition, ancestry, fold, finding, resource sample, checkpoint and event | `data/research/discovery/runs/<run>/lab.sqlite` |
| Checkpoints, research log, progress | `data/research/discovery/runs/<run>/checkpoints/`, `research.log`, `progress.json` |
| Final report | `data/research/reports/discovery_<run>_latest.json` and `.html`, plus a row in `research/experiments.sqlite` |
| Forward paper account | `data/app/discovery_paper.sqlite`, separate from the dashboard's paper account |
| Archived smoke run from the defective build | `data/research/discovery/archive/` |
