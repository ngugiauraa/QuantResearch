# QuantResearch

An XAUUSD quantitative research, market intelligence, machine-learning and
trading platform, built in reviewed phases.

> **Current state: Phase 0 audit complete, awaiting review. Phase 1 has not
> started.** Today the repository is a historical data pipeline only. It has no
> backtester, strategies, Time Machine, risk engine, MT5 adapter, ML, API or
> dashboard. No live or paper orders can be placed by this code.
>
> Research software, not a profit guarantee. Synthetic data is for software
> testing only.

**Audit snapshot:** 2026-11-01. A tick downloader was running for most of the audit
and stopped unexpectedly near its end, so tick figures are for that point in time.
**Language:** Python 3.12 on Windows.
**Initial instrument:** XAUUSD.

## Table of contents

- [1. Audit summary](#1-audit-summary)
- [2. Existing architecture](#2-existing-architecture)
- [3. Repository directory tree](#3-repository-directory-tree)
- [4. Component status](#4-component-status)
  - [4.1 Completed](#41-completed)
  - [4.2 Partially implemented](#42-partially-implemented)
  - [4.3 Broken or unsafe to use](#43-broken-or-unsafe-to-use)
  - [4.4 Missing](#44-missing)
- [5. Data coverage](#5-data-coverage)
- [6. Data-quality findings](#6-data-quality-findings)
- [7. Bid/ask availability](#7-bidask-availability)
- [8. Original trading-skill findings](#8-original-trading-skill-findings)
- [9. Time Machine findings](#9-time-machine-findings)
- [10. Existing test results](#10-existing-test-results)
- [11. Technical debt](#11-technical-debt)
- [12. Dependency assessment](#12-dependency-assessment)
- [13. Recommended implementation sequence](#13-recommended-implementation-sequence)
- [14. Acceptance criteria by phase](#14-acceptance-criteria-by-phase)
- [15. Decisions needed from the project owner](#15-decisions-needed-from-the-project-owner)
- [16. How to run what exists today](#16-how-to-run-what-exists-today)
- [17. Engineering principles](#17-engineering-principles)
- [18. Progress log](#18-progress-log)

---

## 1. Audit summary

| # | Severity | Finding | Evidence |
|---|---|---|---|
| 1 | **Critical** | **The downloader loses the last day of every month, on every timeframe.** The archive has no M1 row dated the 31st, and 204 of 282 M1 months stop before their last weekday. | [Section 6.1](#61-critical-the-last-day-of-every-month-is-missing) |
| 2 | **Critical** | **Every existing quality check passes on this damaged data**, because none of them tests whether all expected days are present. | `parquet_audit_report.json` reports `all_checks_passed: true` |
| 3 | **High** | **Actual ask prices exist.** All sampled tick files from 2003 to 2010 hold both `askPrice` and `bidPrice`, so spreads can be reconstructed. Local tick coverage currently stops at 2010-08. All candle files are bid-only. | [Section 7](#7-bidask-availability) |
| 4 | **High** | **The tick download stopped unexpectedly at 2010-09 with no failure logged.** Restarting it as it stands would keep saving months without their last day, with 25 GB free on C: and tick volume still growing. | [Section 5.3](#53-tick-download-status-and-disk-risk) |
| 5 | **High** | **Time Machine is not a chart replay tool.** The original is a Monte Carlo and history simulator for one US-stock opening-range plan. The specified replay system is new work. | [Section 9](#9-time-machine-findings) |
| 6 | **High** | **Six of the eleven named skills exist in two different versions**, one in each repository. | [Section 8.2](#82-the-two-repositories-and-the-eleven-skills) |
| 7 | **Medium** | Fourteen of the 18 data-pipeline modules and 4 of the 8 test files have never been committed to Git. | [Section 11](#11-technical-debt) |
| 8 | **Medium** | The tests pass (46 of 46) but cover little of the data path: 7 modules have no tests, including feature engineering. | [Section 10](#10-existing-test-results) |

---

## 2. Existing architecture

The system is a set of independent command-line scripts sharing files on disk.
There is no service layer, shared configuration, structured logging or package
installation (`PYTHONPATH=src` is required).

```text
dukascopy-node (npx, unpinned)
        │  monthly CSV per timeframe
        ▼
dukascopy_download.py ──► data/raw/xauusd/<tf>/<year>/*.csv
        │                 data/processed/download_log.jsonl
        ▼
raw_validator.py ────────► raw_validation_report.json, ohlc_anomalies.csv
coverage_audit.py ───────► coverage_audit.json (month level only)
data_quality.py ─────────► quality_report.json
        │
        ▼
quality_pipeline.py ─────► data/processed/parquet/...      accepted rows
        │                  data/processed/quarantine/...   rejected rows
        │                  quality_registry.json
        ▼
parquet_audit.py ────────► parquet_audit_report.json
        │
        ├─► research_db.py / research_cli.py   in-memory DuckDB view "gold"
        └─► feature_engineering.py ──► data/processed/features/v1/...

Separate from this path:
  market_data.py, ingestion.py, session_audit.py   first-generation CSV tools
  resample.py, consolidate.py                      not used by the Parquet path
  data_catalog.py, data_manifest.py, data_sources.py   inventories, now stale
  historical_gold.py                               annual and monthly gold prices
```

Parquet layout:
`instrument=xauusd/timeframe=<tf>/price_side=bid/year=<y>/month=<m>/<source>.parquet`.
Each row carries `source_file` and `source_row_number`. Source files have no
checksums.

Schemas:

| Data | Columns |
|---|---|
| Candles (S1 to MN1) | `timestamp` (UTC), `open`, `high`, `low`, `close`, `volume`, `source_row_number`, `source_file` |
| Ticks | `timestamp` (UTC, ms), `askPrice`, `bidPrice`, `askVolume`, `bidVolume`, `source_row_number`, `source_file` |

Bar `timestamp` is the bar's **open** time. Daily bars start at 00:00 UTC.

---

## 3. Repository directory tree

```text
QuantResearch/
├── config/
│   └── gold_data_catalog.json     tracked; dataset statuses are stale
├── data/                          6.16 GB, 7,314 files; ignored by Git except .gitkeep
│   ├── raw/
│   │   ├── xauusd/                tick s1 m1 m5 m15 m30 h1 h4 d1 mn1 / <year> / *.csv
│   │   ├── gold_historical/       annual and monthly prices, source_manifest.json
│   │   └── xauusd_dukascopy_bid_m1_*.csv   4 early manual downloads, 2025-03 to 2025-06
│   ├── processed/
│   │   ├── parquet/               2,420 files, 50,682,618 rows
│   │   ├── quarantine/            39 files, 119 rejected rows
│   │   ├── features/v1/           2,348 Parquet files, 10,328,016 rows, manifest
│   │   ├── legacy/                1 consolidated M1 Parquet, 7,713,120 rows
│   │   └── *.json, *.csv, *.jsonl registries, audits, download log
│   └── synthetic/                 xauusd_1m.csv, us100_1m.csv (test data)
├── src/quantresearch/             18 modules plus __init__.py (* = never committed)
│   ├── __init__.py                empty
│   ├── market_data.py             candle schema, validation, synthetic generator
│   ├── ingestion.py               CSV import and audit, 1m only
│   ├── session_audit.py           gap classification by weekly schedule
│   ├── data_catalog.py            CSV inventory against the catalogue
│   ├── data_sources.py *          source registry
│   ├── data_manifest.py *         SHA-256 manifest of CSVs
│   ├── dukascopy_download.py *    monthly downloader
│   ├── raw_validator.py *         row-level raw CSV validation
│   ├── coverage_audit.py *        month-level coverage
│   ├── data_quality.py *          pandas CSV validation
│   ├── quality_pipeline.py *      validate, quarantine, write Parquet
│   ├── parquet_audit.py *         Parquet against the registry
│   ├── research_db.py *           DuckDB query layer
│   ├── research_cli.py *          summary, coverage, query, sql commands
│   ├── feature_engineering.py *   feature v1 (basic price features)
│   ├── resample.py *              M1 CSV to higher timeframes
│   ├── consolidate.py *           single-file Parquet consolidation
│   └── historical_gold.py *       long-run gold price download
├── tests/                         8 unittest files (* = never committed)
│   ├── test_data_catalog.py       test_ingestion.py
│   ├── test_market_data.py        test_session_audit.py
│   └── test_data_pipeline.py *    test_data_quality.py *
│       test_quality_pipeline.py * test_raw_validator.py *
├── venv/                          ignored
├── .gitignore
├── README.md
├── README_SETUP.md *              to be removed after approval; content is in Section 16
├── requirements.txt               modified, uncommitted
└── requirements-lock.txt *        stale
```

Git: branch `main`, remote `origin`, 5 commits, head `9f58334`. There is no
frontend, no database migration and no CI configuration.

---

## 4. Component status

### 4.1 Completed

These work and were checked in this audit, within the limits stated.

| Component | File | Evidence |
|---|---|---|
| Candle schema and synthetic data | `market_data.py` | 7 tests pass. Limited to 1m and includes out-of-scope US100. |
| Single-CSV import and audit | `ingestion.py` | 7 tests pass. 1m only. |
| Session gap classification | `session_audit.py` | 7 tests pass. Schedule limits in Section 6.4. |
| Row-level raw validation | `raw_validator.py` | 7 tests pass; last run covered 2,411 files. |
| Validation, quarantine and Parquet writing | `quality_pipeline.py` | 6 tests pass. Parquet row counts match the registry (50,682,618). |
| Parquet integrity audit | `parquet_audit.py` | Last report: 2,420 of 2,420 pass, no orphans. No unit tests. |
| DuckDB read access | `research_db.py` | `summary()`, `coverage()` and `query()` were run against all 2,420 files. |

"Completed" means the code does its narrow job. It does not mean the data is
correct: see Section 6.1.

### 4.2 Partially implemented

| Component | File | What is missing |
|---|---|---|
| Historical download | `dukascopy_download.py` | Works and resumes, but has the month-end defect. Failed months stay as holes, version is unpinned, no checksums. |
| Timeframe coverage | archive | D1 to M1 cover the full range. Tick and S1 are partial. |
| Feature engineering | `feature_engineering.py` | Returns, ATR, volatility, candle shape. Tick features exist in code but were never built. No RSI, moving averages, Bollinger Bands, market structure, SMC/ICT or macro features. **No tests.** |
| Data catalogue | `data_catalog.py`, `config/` | Runs, but the statuses are stale: `gold_bid_ask` says `not_acquired` and `technical_features` says `not_built`. Targets `1w`, omits S1 and MN1. |
| Source registry | `data_sources.py` | Dictionary only. Calls Dukascopy "bid-side" and `1m` only, and neither is correct. No adapters. |
| Provenance | `data_manifest.py` | Last run lists only 6 files, 3 of them under outdated names. The monthly archive has no checksums. |
| Long-run gold prices | `historical_gold.py` | Downloaded to 2026-09 with a checksum. No tests. Source notes that 1833-1959 monthly values repeat annual averages. |

### 4.3 Broken or unsafe to use

| Component | Problem | Consequence |
|---|---|---|
| `dukascopy_download.py` | Passes the month's last day as an exclusive `-to` date. | **All monthly files lose the last day.** Section 6.1. |
| `coverage_audit.py`, `parquet_audit.py`, `raw_validator.py` | No day-level completeness check. | Damaged data reports as clean. |
| Parquet tick and S1 layers | Stale. 34 of 70 tick CSVs and 11 of 47 S1 CSVs are not converted. | Queries silently stop at 2006-08. |
| `features/v1` | Built on truncated data, with no tick or S1 features. The output path is hardcoded and refuses to rebuild. | Rolling values and returns around month ends are wrong. |
| `consolidate.py` | **Do not run.** Keeps the last of any duplicate timestamps without reporting them, applies no OHLC quarantine, and writes into the live Parquet root in a different layout. | `research_db` would count those rows twice. |
| `resample.py` | Reads one normalized CSV and defaults to one month. Daily bars expect 1,440 rows, so they read as incomplete on any shortened trading day. Uses `1d` naming, unlike `d1`. | Not usable for the warehouse. |
| `research_db.query()` | `query("tick", price_side="ask")` returns nothing, because ask data sits in a partition labelled `bid`. | Ask data looks absent. |
| `research_db` read-only claim | `read_only` is ignored and `sql()` runs any statement. | `sql()` can write files, despite the "read-only" docstring. |
| `quality_registry.json` | Edited by a reconcile step that is not in the repository. `rows_validated` now means rows read, but in code it means rows accepted. `files_passed` is a stale 0 beside `files_pass`. | Totals are misleading. |
| `download_failures.json` | Overwritten by each run. It lists only `mn1 1999-06`, while 43 tick and S1 months failed and never succeeded. | Hides the real failures. |
| `download_log.jsonl` | 2 corrupted lines (2762 and 2912), which suggests concurrent writers. Parsers fail unless they tolerate this. | Log cannot be parsed strictly. |

### 4.4 Missing

Everything below has no implementation in the repository.

- **Data:** ask-side candles, macro, cross-market, COT, ETF flows, news, event/publication/ingestion/revision timestamps, incremental partition management.
- **Features:** technical indicators beyond v1, SMC/ICT and market structure, microstructure beyond spread, macro and cross-market.
- **Simulation:** event-driven backtester, cost and slippage models, Time Machine, look-ahead integrity test, replay viewer.
- **Research:** the seven strategy engines, statistics laboratory, walk-forward validation, experiment records.
- **Trading:** decision engine, independent risk engine, MT5 adapter, paper trading, trading modes, credential storage.
- **Skills:** none of the eleven skills is integrated.
- **Intelligence:** ML pipeline, model registry, knowledge layer.
- **Application:** API, frontend, application database, monitoring.
- **Engineering:** package install, configuration system, structured logging, CI, end-to-end tests.

---

## 5. Data coverage

Sources: live file scans and Parquet queries on 2026-11-01. Rows are for
Parquet unless stated. Provider is Dukascopy through `dukascopy-node` 1.51.1,
per the download log and downloader code.

### 5.1 By timeframe

| Timeframe | Raw CSV files | Parquet files | Rows | First | Last | Status |
|---|---:|---:|---:|---|---|---|
| Tick | 70 | 36 | 24,756,163 | 2003-05-05 | Parquet 2006-08-30; raw 2010-08-30 | Partial, download stopped |
| S1 | 47 | 36 | 15,598,439 | 2003-05-05 | Parquet 2006-08-30; raw 2008-03 | Partial, download stopped |
| M1 | 282 | 282 | 7,713,120 | 2003-05-05 | 2026-10-28 | All months present, last days missing |
| M5 | 282 | 282 | 1,614,760 | 2003-05-05 | 2026-10-28 | Same |
| M15 | 282 | 282 | 544,807 | 2003-05-05 | 2026-10-28 | Same |
| M30 | 282 | 282 | 273,369 | 2003-05-05 | 2026-10-28 | Same |
| H1 | 282 | 282 | 137,255 | 2003-05-05 | 2026-10-28 | Same |
| H4 | 282 | 282 | 36,458 | 2003-05-05 | 2026-10-28 | Same |
| D1 | 329 | 329 | 7,920 | 1999-06-03 | 2026-10-28 | Same; 118 rows rejected |
| MN1 | 328 | 327 | 327 | 1999-07-01 | 2026-10-01 | 1999-06 never downloaded; 2000-04 rejected; 2026-10 is an incomplete month |

The total is 50,682,618 Parquet rows.

Other data: annual and monthly long-run gold prices (monthly runs 1833-01 to
2026-09, not from Dukascopy, no OHLC) and a legacy M1 Parquet with the same row
count, 7,713,120, and the same date range. Its rows were not compared one by one.

### 5.2 Holes in tick and S1

Of 88 months from 2003-05 to 2010-08, **18 tick months are missing**. The log
shows each failed all its retries and never succeeded:

| Year | Missing tick months |
|---|---|
| 2005 | 03, 04, 07, 08 |
| 2007 | 03, 04, 05, 06, 07, 08, 09, 10 |
| 2008 | 09, 10, 11 |
| 2009 | 12 |
| 2010 | 02, 03 |

The last job started at 2008-08, so **re-running it with the same arguments would not
retry the 2005 and 2007 holes**. S1 has its own holes: 2005-02, 03, 07 and 08, all of 2007-03 to 2007-10,
and nothing from 2008-04 onwards. Its last recorded failure is 2009-04, and no S1
download is running now.

The log records 166 failed tick attempts and 112 failed S1 attempts, mostly
non-zero exits. The cause is not known, because each attempt's log is deleted
with its temporary folder.

### 5.3 Tick download status and disk risk

This process was running when the audit began, and the audit did not touch it:

```text
python -m quantresearch.dukascopy_download --timeframe tick --price bid \
    --start 2008-08-01 --end 2026-10-31 --timeout 1800 --retries 4
```

It started on 2026-10-31 at 18:12. **By 23:35 on 2026-11-01 the whole process tree had
gone**, while working on 2010-09.

- **How it ended:** its partial file last changed at 23:19:28 (55 MB), and the log has
  no success or failure entry for 2010-09, so it did not exit through its own error
  handling. No audit command targeted it. Windows logged power-state events at about
  23:25. **The cause was not determined.**
- **It inherits the month-end defect.** Every month it saved lacks its last day.
- **Disk:** 25 GB is free on C:. About 194 months remain. Monthly size rose from
  17 MB (2009-09) to 66 MB (2010-08). At a flat 66 MB the remainder is about
  13 GB. Tick activity after 2010 has not been observed locally, so if months
  average about 130 MB or more, the disk fills before the job ends. That threshold
  is arithmetic, and the real sizes are unknown.
- **Leftovers:** 8 abandoned `.part` files (78 MB) and 3 dead temporary folders
  remain under `data/raw/xauusd`, including the interrupted 2010-09 attempt.

---

## 6. Data-quality findings

### 6.1 Critical: the last day of every month is missing

**Cause.** `month_chunks()` produces each month's last day as the end date, and
`dukascopy_download.py` passes it as `-to`. `dukascopy-node` treats that date as
midnight at the start of that day, so the final day is excluded. The downloader's
own log shows this:

```text
From date:      Sep 1, 2010, 12:00:00 AM
To date:        Sep 30, 2010, 12:00:00 AM
```

**Evidence.**

| Check | Result |
|---|---|
| M1 rows dated the 31st, whole archive | **0** |
| M1 months short of their last weekday | **204 of 282**: 202 by exactly one day, plus the unfinished 2026-10 and one earlier month. In the other 78 the last weekday is present because the month ends on a weekend, and any Sunday-evening session on that last day is still lost. |
| D1 months short of their last weekday | 241 of 329 |
| Spot checks on M1: 2024-01-31, 2024-02-29, 2024-05-31, 2025-07-31, 2025-09-30, 2026-03-31, 2026-06-30, 2026-09-30 | **0 rows on each** |
| D1 weekdays missing in 2024 | Nine, and all are the final weekday of a month |
| March 2025: early manual file against the archive | Manual file ends 2025-03-31T23:59 with 29,073 rows. Archive file ends 2025-03-30T23:59 with 27,693. **1,380 rows lost.** |

The M1 loss is roughly 3 % of rows (202 months × about 1,137 rows, an estimate).
The same defect affects tick and S1.

**Why this matters more than 3 % suggests.**

- **Strategy 5 is directly compromised.** Month-end flows and turn-of-month are
  what it studies, and those exact days are absent.
- **It creates false price gaps, which would contaminate Strategy 7.** **8 of the
  12 largest M1 moves in the archive are this artefact**, spanning a missing day
  rather than one minute. Examples: −4.78 % at 2012-03-01 and +4.00 % at
  2026-04-01.
- **Strategy 6 loses calendar cells** and sees month-end effects it cannot measure.
- **Backtests would hold positions through a day that does not exist**, skipping
  stops and targets.
- **Features are wrong** after each missing day.

**Why nothing caught it.** Validators check that rows are well formed, and
coverage is checked per month. The existing test
`test_month_chunks_cross_year_boundary` asserts the end date that causes this,
so it passes.

**Status.** Not fixed in Phase 0. The repair is Phase 1 step 1. No estimated data
should fill these days, since the source data can be downloaded correctly.

### 6.2 Rejected rows

Quarantine holds 119 of 50,682,737 rows read. All are `LOW_ABOVE_OHLC_MIN`, where
the close is below the reported low.

| Timeframe | Rows | Range | Size of violation |
|---|---:|---|---|
| D1 | 118 | 1999-06-15 to 2002-10-01 | Across both: median 0.30, maximum 3.00 |
| MN1 | 1 | 2000-04-01 | |

All predate the intraday data and leave holes in early D1. They are preserved,
not repaired. Early D1 data looks like a different feed: two-decimal prices and
volumes around 2 billion, against about 36 million in 2026. Its provenance is
unverified.

### 6.3 Duplicates and ordering

There are no duplicate timestamps in any Parquet timeframe and no out-of-order
rows in the registry. The full-archive duplicate check covers the converted
Parquet only, and duplicates in unconverted tick months are not ruled out beyond
the 15 sampled files (no duplicates in up to 200,000 rows each).

### 6.4 Gaps and session structure

Gaps are observations, not automatically defects.

| Finding | Detail |
|---|---|
| Sparse early M1 | About 529 rows per active day in 2003, 886 in 2005, and 1,100 to 1,170 from 2007. Early research must handle thin data. |
| Daily break changes over time | 61-minute gaps appear from 2013 (135 to 195 per year) and almost never before. `session_audit.py` applies one fixed schedule and **has no history of schedule changes.** |
| No holiday calendar | Long closures occur around Christmas, New Year and Good Friday. They are not modelled, and the longest gaps also include the missing month ends. |
| Flat periods are dropped | The downloader reports `Include flats: false`, so a gap can mean an unchanged price rather than missing data. |
| Recent short gaps | Isolated gaps of 5 minutes or less are few in recent years (27 in 2025), with some unexplained peaks such as 681 in 2019. |

### 6.5 Price outliers

M1 has 12 moves above 2 %. Eight are the month-end artefact, one spans a normal
weekend (2020-03-15), and 3 are on consecutive minutes: 2021-08-08 22:57 (−2.69 %),
2015-07-20 01:29 (−2.40 %) and 2026-01-29 15:27 (−2.40 %). D1 has 7 moves above
7 %, including −10.5 % on 2026-01-30 and +10.4 % on 2008-09-17. Prices were
checked only for internal consistency. **None has been verified against an outside
source**, and no outlier detection exists in the pipeline.

### 6.6 Cross-timeframe consistency

| Check | Result |
|---|---|
| Tick bid to M1 OHLC, 2005-06 | **Exact on all 22,874 bars.** Ask does not match, so candles are bid. |
| Tick bid to S1 close, 2005-06 | **Exact on all 522,604 seconds.** |
| Native H1 to M1, 2015-03 | Exact on 483 joined hours. 6 H1 bars have no M1 rows, all at 21:00 UTC and mostly with thin volume. |
| Native H1 to M1, 2025-03 | Open differs on 3 of 462 hours, close on 4. Mean close difference 0.0016. |

So bars can be rebuilt from ticks wherever ticks exist. This was tested on single
months, not the whole archive.

### 6.7 Other cautions for later phases

- **Volume is not market volume.** Average M1 volume ranges from about 5,400 (2008)
  to about 58,000 (2016), so its scale is not comparable across years.
- **Bar timestamps are open times.** A feature stamped at a bar's open but computed
  from its close is only known at the bar's end. A naive join across timeframes
  would leak future data. This is the main look-ahead risk for Phase 1.
- **Feature windows warm up silently.** `atr_14` and similar values are emitted on
  fewer rows than their window, and rolling windows run across weekends and closures.
  By inspection the feature SQL uses trailing windows only, with no future data in
  the windows themselves.
- **Tie order is undefined.** Features order by `timestamp` alone. Any duplicate
  timestamps would make results non-reproducible.
- **Daily bars include Sundays** and use UTC day boundaries, not a 17:00 New York
  session close.
- **The last bars can be partial.** The archive ends 2026-10-28 and marks no bar as
  incomplete.

---

## 7. Bid/ask availability

**The historical dataset is not bid-only.** The `bid` in tick filenames and
partitions is misleading.

| Question | Answer | Basis |
|---|---|---|
| Do tick files contain both prices? | **Yes** | All 15 raw files sampled, 2003-05 to 2010-08. Full scan of 24,756,163 Parquet ticks: no nulls. Full scan of the 2010-08 CSV: no null asks. |
| Are the two sides aligned? | **Yes, per record** | Each row has one timestamp with both prices. The file cannot show whether the provider updated both sides at that exact moment. |
| Can spreads be reconstructed? | **Yes**, for months with ticks | Spread = `askPrice − bidPrice`. No negative spreads in any scan or sample, and no zero spreads in the Parquet scan or the samples. |
| Do other timeframes contain ask? | **No** | No file or path contains ask data. S1 to MN1 are all bid. |
| Coverage of observed ask today | 70 months, 2003-05 to 2010-08 | With the 18 holes in Section 5.2 and each month's last day missing |
| After 2010-08? | **Not yet known** | Not downloaded. Its presence is expected but unverified. |

Sample method: the header and the first 200,000 rows of each of the 15 sampled raw
files were checked for blanks and spread values. The whole archive was not scanned.

Observed spread, in USD:

| Year | Rows | Mean | Median | Min | Max | Distinct values |
|---|---:|---:|---:|---:|---:|---:|
| 2003 | 2,734,851 | 0.440 | 0.41 | 0.16 | 0.706 | 64 |
| 2004 | 7,081,279 | 0.448 | 0.41 | 0.16 | 0.706 | 79 |
| 2005 | 6,775,262 | 0.451 | 0.46 | 0.16 | 0.706 | 77 |
| 2006 (to Aug) | 8,164,771 | 0.452 | 0.46 | 0.16 | 0.708 | 107 |
| 2010-08 | 1,282,640 | n/a | 0.504 | n/a | 0.962 | 235 |

**Interpretation.**

- Spreads sit on a small set of repeated values (0.404 and 0.504 are the most
  common in 2005). They look provider-processed, so they are real recorded quotes
  but not necessarily a live market's raw spread.
- They are Dukascopy's spreads. **The account used through MT5 will differ.** Costs
  should be calibrated against that broker before results are trusted.

**Spread policy for later phases.**

1. Use tick bid and ask wherever they exist, labelled `observed_tick`.
2. Fill remaining periods with real data first: finish the tick download, or fetch
   ask candles. The downloader accepts `--price ask` but this was **not run or
   verified** in this audit.
3. Use an estimated model only for periods where real ask data proves unavailable.
   It must be configurable and labelled `assumed`, and never reported as observed.

No estimated spread model is proposed now, because actual ask data has not yet
been shown to be unavailable.

---

## 8. Original trading-skill findings

### 8.1 Verification

| Item | Result |
|---|---|
| Original repository | https://github.com/Jakeschincariol/trader-skill. Verified, MIT, commit `5189e79` (2026-10-14). |
| Day Trader repository | **Verified:** https://github.com/Jakeschincariol/day-trader-skill. MIT, commit `3d0f4ef` (2026-10-20). Listed on the author's GitHub profile, and its README calls it the sequel to `trader-skill` ("the Trader skill v2"). |
| Licence | MIT, copyright 2026 Jake Schincariol. The notice must be kept with any reused code. |

Both were cloned to a temporary folder outside the project. Nothing was added to
this repository.

**Depth of inspection.** Read in full: the skill definitions for all 12 components
below (v2 where both exist) and both READMEs. `timemachine.py` was read through its
core logic. For the other tools, and for the v1 versions of the dual-version skills,
structure, descriptions, commands and network endpoints were inspected, not every
line. **Upstream test suites and tools were not executed**, so their behaviour on
Python 3.12 is untested here.

### 8.2 The two repositories and the eleven skills

Each repository ships its own set of eleven, and 7 folder names appear in both. The
eleven named in the project specification span the two repositories.

| Skill in specification | Upstream folder | `trader-skill` (v1) | `day-trader-skill` (v2) |
|---|---|:---:|:---:|
| Trader News | `trader-news` | Yes | Yes, differs |
| Trader Risk | `trader-risk` | Yes | Yes, differs |
| Trader Budget | `trader-budget` | Yes | Yes, differs |
| Trader Broker | `trader-broker` | Yes | Yes, differs |
| Trader Alerts | `trader-alerts` | Yes | Yes, differs |
| Trader Analyst | `trader-analyst` | Yes | Yes, tool nearly identical, instructions differ |
| Trader Screener | `trader-screener` | **Only here** | No |
| Trader Copycat | `trader-copycat` | **Only here** | No |
| Day Trader Scanner | `trader-scanner` | No | **Only here** |
| Entry Timer | `trader-entry` | No | **Only here** |
| Exit Planner | `trader-exit` | No | **Only here** |
| Time Machine (separate) | `trader-timemachine` | No | **Only here** |

**Not in the specification:** Pelosi Tracker (both), Tax Optimizer and Rebalancer
(v1 only). These are excluded unless the owner adds them.

**The versions are materially different.** v1 Risk and Budget serve long-term
portfolio allocation. v2 adds per-trade loss limits, bracket orders, daily loss,
trades per day, entry windows and reward-to-risk. Which to use is decision D2 in
Section 15.

### 8.3 What each component is

**All twelve are hybrids:** a `SKILL.md` instruction file that an AI agent follows,
plus a standard-library Python tool. No tool uses `subprocess` or `eval`. Decision
logic lives in Python. Workflow, reporting rules and approval steps live in
`SKILL.md`, so **those steps do not exist as code**. They exchange JSON files in a
`trader/` folder.

| Skill | Version read | Does | Inputs | Outputs | External dependencies |
|---|---|---|---|---|---|
| News | v2 | New headlines and 8-Ks per ticker, tags, moves of 3 % or more, deduplicates | Tickers, watchlist, plan, portfolio | Report, `state/news.json` | Yahoo RSS and chart (unofficial), SEC EDGAR with user agent |
| Risk | v2 | PASS or BLOCK per order; exits are never blocked by day limits | Orders, config, portfolio, prices, broker log | `state/risk.json` with order fingerprints | None |
| Budget | v2 | Whole-share size from the smallest of four limits; one bracket order, or weights mode | Plan, config, prices, portfolio | `orders.json` in Alpaca's format | None |
| Broker | v2 | Renders tickets, records a typed yes, logs fills, builds flatten tickets. **`ticket.py` never contacts a broker** | Orders, risk verdicts, plan | `state/broker.json`, `portfolio.json` | Order placement is the agent calling Alpaca's MCP server, or the IBKR connector |
| Alerts | v2 | Level, percent, time and opening-bell alerts, each ringing once | Plan, rules, portfolio | `state/alerts.json`, `prices.json` | Yahoo chart; Stooq (upstream notes a 404 on 2026-10-13) |
| Analyst | v2 | Quarterly results and margins from SEC XBRL | Tickers | `metrics.json` | SEC EDGAR with user agent |
| Screener | v1 | Applies JSON rules, explains each pass and fail, ranks | Metrics JSON or CSV, rules, prices | `shortlist.json` | None |
| Copycat | v1 | Rebuilds a fund's portfolio from its 13F-HR, quarter-on-quarter diff, target weights | Filer or CIK | `targets.json` | SEC EDGAR with user agent; OpenFIGI optional |
| Scanner | v2 | Filters about 5,900 US stocks; daily setups (20-day breakout, bull flag, reclaim MA20, oversold, bounce) and opening-range breakouts | Universe, config | `watchlist.json` | Nasdaq Trader lists, Yahoo chart and spark |
| Entry Timer | v2 | First completed bar closing above the range or a level, optional volume check. Long only | Symbol, config | `entry.json` | Yahoo chart |
| Exit Planner | v2 | Stop (range mid, range low, price, percent, ATR), target in R, flat-by time; refuses bad plans. Long only | `entry.json`, config | `plan.json` | Yahoo chart for ATR stops only |
| Time Machine | v2 | See Section 9 | `plan.json` | `timemachine.json` | Yahoo chart |

Upstream tests are offline and use real fixtures: v2 has 315 test functions and v1
has 175 across all their skills, including those outside this project.

### 8.4 What can be reproduced, and what cannot

| Skill | Reusable directly | Must be adapted | Not applicable to spot gold |
|---|---|---|---|
| Risk | Check structure, fingerprints, sequential batch simulation, exits never blocked | Asset check rejects anything but US tickers, so **it would block XAUUSD**. Shares become lots with contract specs. NYSE window becomes the XAU session. Short side. | PDT rule, cash-account logic |
| Budget | Smallest-of-limits sizing, refusals | Lot sizing, margin, MT5 order format | Alpaca order format |
| Broker | Ticket, approval and audit flow | MT5 replaces the MCP connector, and approval steps must become real code | Alpaca and IBKR connector setup |
| Alerts | Rule engine, ring-once state | XAU session calendar, MT5 or warehouse quotes | NYSE holiday tables |
| Entry Timer | Closed-bar trigger rule | Define "opening range" for a near-24-hour market (session choice). Short side. | NYSE hours |
| Exit Planner | Stop and target rules, refusals, rounding | XAU tick size, spread-aware minimum risk, short side, session flat-by | |
| Scanner | Setup rules (RSI, breakouts, flags) | Becomes a multi-timeframe scan of one instrument | Universe of 5,900 stocks |
| News | Dedup state, tags, "headlines are leads" rules | Gold and macro sources. Yahoo RSS for gold tickers is untested. | 8-K logic for a commodity, except miners and ETFs |
| Time Machine | Simulation algorithm and verdict logic | Section 9 | |
| Screener | Rules engine, works on any metrics | Point it at miners, gold ETFs or strategy metrics | Stock-picking as a purpose |
| Analyst | SEC XBRL pipeline | Only for gold miners and related companies, as context | **XAUUSD has no earnings or filings** |
| Copycat | 13F parsing, diffs | Use to observe funds' GLD, IAU and miner positions, for Strategy 5 | **13Fs do not cover spot gold**. Portfolio copying does not map onto a single XAUUSD position. |

**Limits worth stating plainly.**

- Copycat data is up to 45 days old on filing. For point-in-time use, the filing date
  must be the availability date.
- Both long-only skills and the `allow_short: false` default conflict with trading
  XAUUSD in both directions. Adding shorts extends upstream behaviour and should be
  recorded as an adaptation.
- Upstream never allows an order without a typed yes, and forbids orders from
  background tasks. **This conflicts with the specified automated mode** (decision D3).
- Yahoo endpoints are free and unofficial, so they are not a dependable source for
  trading decisions.

---

## 9. Time Machine findings

**The original is not a historical replay tool.** It has no chart, play, pause,
stepping or speed control. Its purpose is one pre-trade question: how often does this
plan hit its target first, its stop first, or neither?

**Its actual method** (`trader-timemachine/timemachine.py`, 956 lines, 47 upstream
test functions):

1. Takes one plan: symbol, date, entry, stop, target, entry bar, opening range.
2. Fetches 60 days of 5-minute Yahoo bars and keeps only full 78-bar sessions
   (9:30 to 16:00 New York) **strictly before the plan's date**. Needs at least 20.
3. Builds a pool per bar slot, with each past bar stored as OHLC ratios to the previous
   close.
4. Runs 10,000 replays (default seed 19). Each draws a past bar from the same time of
   day, within ±2 slots, scaled to the current price.
5. In each bar the stop is checked before the target, so a bar touching both counts as
   a stop. A gap through the stop still counts as −1R.
6. Separately re-runs the same rule on every real past session. This history replay
   supports opening-range setups only.
7. Reports odds, the median and 5th to 95th percentile close, average R, fan-chart
   bands (a 2,000-path run), 160 sample paths, a 24-bin histogram, and a verdict.
8. The verdict compares target share with break-even at `1 / (1 + R)`: under 5 points
   is a coin flip or break-even, 5 to 15 "leans", 15 or more "strongly".

**Assessment.**

| Topic | Finding |
|---|---|
| Look-ahead | Sound in design: the pool excludes the plan's day and later, and unfinished bars are dropped. |
| Statistical limit | Bars are drawn independently, so volatility clustering and autocorrelation between bars are lost. For gold, where volatility clusters, its odds need testing against real history before being relied on. |
| Execution realism | No spread, slippage or commission. Fills are at exact prices. |
| Fixed to US equities | 78-bar session, NYSE calendar, Yahoo data, 60-day pool, whole cents. |

**What this means for the project: two separate deliverables.**

| | A. Outcome Simulator | B. Historical Replay |
|---|---|---|
| Basis | Port of the original algorithm and verdict logic | New, from the specification |
| Purpose | Odds for one plan before a trade | Step through history with strategies, orders and logs |
| Adaptation | XAU session template, local warehouse instead of Yahoo, longer pool, costs | Play, pause, step, speed, tick replay, event log, look-ahead test |
| Phase | 4A, with the skills | Foundation in Phase 1, orchestration in 4B |

Keeping the original's seeded reproducibility and its plain verdicts is recommended
in both.

---

## 10. Existing test results

**Run on 2026-11-01 in the project venv, with `PYTHONDONTWRITEBYTECODE=1`:**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests -v
```

**Result: 46 tests run, 46 passed, 0 failed, 0 errors, 0.41 s.**

**pytest could not be used.** It is not installed in the venv or the system Python, and
installing it was outside the Phase 0 rules. The `-p no:cacheprovider` setting therefore
did not apply. All tests are `unittest.TestCase` classes and ran under the project's
documented `unittest` runner.

| Test file | Tests | Passed | Writes to project? |
|---|---:|---:|---|
| `test_data_catalog.py` | 5 | 5 | No, temp folders only |
| `test_data_pipeline.py` | 6 | 6 | No, temp folders only |
| `test_data_quality.py` | 1 | 1 | No |
| `test_ingestion.py` | 7 | 7 | No, temp folders only |
| `test_market_data.py` | 7 | 7 | No, temp folders only |
| `test_quality_pipeline.py` | 6 | 6 | No |
| `test_raw_validator.py` | 7 | 7 | No, temp folders only |
| `test_session_audit.py` | 7 | 7 | No, temp folders only |

All test files were read before running. Afterwards `git status` was unchanged, no cache
folder appeared and no new bytecode was written.

**Other read-only runs:** `GoldResearchDB.summary()`, `coverage()` and `query()`, from a
temporary working directory: all succeeded.

**Modules with no direct tests:** `feature_engineering`, `research_db`, `research_cli`,
`parquet_audit`, `coverage_audit`, `consolidate`, `historical_gold`. Of
`dukascopy_download`, only `month_chunks` is tested, and that test encodes the defect.

**Not executed by design:** downloaders, `quality_pipeline`, `consolidate`,
`feature_engineering`, `raw_validator`, `coverage_audit`, `data_catalog` and
`data_manifest` entry points, since they write outputs.

**Bugs found by this audit:** see Sections 4.3 and 6.1. **No code was changed.**

**Environmental notes:**

- The tick downloader wrote to `data/raw` and `download_log.jsonl` during the audit
  until it stopped. Those changes are not from this audit. See Section 5.3.
- DuckDB 2.0 emits a pandas `Pandas4Warning` on `.df()` and has removed
  `relation.fetchone()`. The project's code calls `connection.execute()`, which was
  unaffected in these runs.

---

## 11. Technical debt

| Priority | Item | Action |
|---:|---|---|
| 1 | 14 modules and 4 test files have never been committed, so no copy exists outside this PC | Commit once Phase 1 is approved |
| 2 | No day-level completeness check | Add a trading-calendar audit |
| 3 | No checksums on the archive | Add per-file SHA-256 to the registry |
| 4 | Six overlapping validators and audits | Merge into one pipeline |
| 5 | Two generations of ingestion code | Retire `ingestion.py` paths or scope them to imports |
| 6 | Downloader depends on an unpinned `npx --yes` package | Pin the version and record it per file |
| 7 | Attempt logs are deleted with their temp folders | Keep failure logs |
| 8 | Temp download folders sit inside the raw archive, so `rglob("*.csv")` can see in-flight files | Move outside the archive |
| 9 | Tick data partitioned as `price_side=bid` | Use a quote-type partition |
| 10 | Tick CSVs use about three times the disk of Parquet | Stage and compress |
| 11 | Row-by-row Python timestamp loops | Vectorize; needed before scanning all ticks |
| 12 | No package install, config system or structured logging | Add `pyproject.toml`, config and logging |
| 13 | `US100` appears in the schema and generator, out of scope | Remove from the active scope |
| 14 | Relative paths in `raw_validator` and `data_quality` depend on the working directory | Use project-root paths |
| 15 | `README_SETUP.md` breaks the one-Markdown-file rule | Delete after approval. Its content is in Section 16. |

---

## 12. Dependency assessment

**Runtime found:** Python 3.12 venv, Node 22.23.3, 6 CPU threads, 15.3 GB RAM, 25 GB free on C:.

| Package | `requirements.txt` | Lock file | Installed | Note |
|---|---|---|---|---|
| tzdata | >=2025.2 | 2026.6 | 2026.6 | |
| pandas | >=2.2 | 3.1.0 | 3.1.0 | |
| pyarrow | >=17.0 | **absent** | 26.0.0 | Lock is stale |
| duckdb | >=1.4.0 | **absent** | 2.0.0 | Major version beyond the floor; API changes seen |
| numpy | none | 2.5.4 | 2.5.4 | Transitive |
| python-dateutil | none | 3.0.0 | 3.0.0 | Transitive |
| six | none | 1.17.0 | 1.17.0 | Transitive |
| pytz | none | **absent** | 2026.6 | Transitive |
| dukascopy-node | not recorded | not recorded | 1.51.1 (npx cache) | Unpinned, fetched at run time |

The installed set cannot be reproduced from the lock file. The change to
`requirements.txt` is uncommitted.

**Not installed, needed later:**

| Phase | Packages |
|---|---|
| 1 | pytest, a chart library or lightweight web server for the viewer |
| 2 | scipy, statsmodels, scikit-learn |
| 3 | MetaTrader5, secure credential library |
| 4A | HTTP client, feed parsing as needed |
| 4B | LightGBM or XGBoost; PyTorch only if justified; retrieval tooling |
| 5 | FastAPI, uvicorn, Node toolchain for Next.js, Lightweight Charts |

**MT5:** the `MetaTrader5` package is absent. MetaQuotes user folders exist in the
Windows profile, but no terminal was running. Connectivity and broker access are untested.

**External access needed:** a SEC user agent with the owner's real name and email for
SEC-based skills, broker login for MT5 (entered only by the owner, never in chat or
files), and API keys for any macro providers chosen in 4A (for example FRED).

---

## 13. Recommended implementation sequence

Dependency order within Phase 1 matters, because every later result depends on the data.

| Step | Work | Why here |
|---:|---|---|
| 1.0 | Commit existing work as a rollback point. Confirm no download is running (D1). | Protects uncommitted code |
| 1.1 | **Fix the month-end defect**, with a regression test using the complete 2025-03 manual file | Everything else depends on it |
| 1.2 | Add trading-calendar completeness audits, with session history and holidays | Proves the fix, prevents recurrence |
| 1.3 | Re-download candles into a new versioned location. Keep the old set until verified. | No destructive overwrite |
| 1.4 | Tick pipeline: pinned downloader, compressed staging, hole retries, checksums | Disk and reliability |
| 1.5 | Rebuild S1 and ask/bid candles from ticks where available, comparing against native bars | Observed spreads; S1 may not need separate downloads |
| 1.6 | Unify validation, registry, catalogue and DuckDB access | Removes contradictory reports |
| 1.7 | Point-in-time layer: bar-close availability, event and publication timestamps | Must precede features |
| 1.8 | Feature v2: technical, market structure, microstructure, each tested | Depends on 1.7 |
| 1.9 | Event-driven backtester with labelled cost sources | Depends on data and features |
| 1.10 | Time Machine replay foundation | Shares the engine |
| 1.11 | Mandatory look-ahead integrity test | Gate for Phase 2 |
| 1.12 | Replay debugging viewer | Debugs the above |

Then **Phase 2** (strategies), **Phase 3** (risk, MT5, modes), **Phase 4A** (skills, Outcome
Simulator, external data), **Phase 4B** (ML, knowledge, orchestration), **Phase 5** (dashboard).

Dependencies that matter for planning:

- Strategy 3 (statistical arbitrage) needs cross-market data, which the current order delivers in
  4A. Phase 1 should therefore acquire a minimal cross-market set (for example DXY or EURUSD,
  silver and yields), or Strategy 3 can only be partially completed in Phase 2.
- Strategy 5 needs ETF, COT and futures data, and Strategy 6 needs an economic calendar. The same
  applies. Their Phase 2 scope should state what is possible without them.
- Macro and news features in Section 1.3 of the specification depend on the same 4A data sources.

---

## 14. Acceptance criteria by phase

Each phase also needs: tests actually executed, results recorded here, limitations
documented, this README accurate, and explicit owner approval.

### Phase 1: Core infrastructure and Time Machine foundation

| Area | Criterion |
|---|---|
| Month-end defect | M1 has rows on the last trading day of every month, including the 31st. New data for 2025-03 matches the complete manual file. |
| Completeness | Audit reports expected and actual trading days per timeframe, with session history and holidays. It **fails** on the current archive. |
| Coverage | Tick, S1, M1, M5, M15, M30, H1, H4, D1 and MN1 are present, or each remaining gap is listed with its reason. |
| Safety | No dataset overwritten without a verified backup. Rejected rows and anomaly reports preserved. |
| Provenance | Every file has a checksum, downloader version, download time and source. |
| Spreads | Each bar and simulated fill labels its cost source: `observed_tick`, `observed_ask_candle` or `assumed`. |
| Point in time | A bar's values are unavailable until it closes. Event, publication, ingestion and revision timestamps are separate. |
| Features | Every feature has a definition, timestamp convention, warm-up rule and a test. Results do not depend on row-tie order. |
| Backtester | Tests cover spread, slippage, commission, delay, gaps through stops, stop-and-target in one bar, and position sizing, with hand-calculated results. |
| Replay | Play, pause, resume, reset, speed, step and navigation work. The same seed and inputs reproduce the same session. |
| **Look-ahead test** | Run through T, truncate at T, re-run: every output at or before T is identical, except documented nondeterminism. Run on multiple dates, including month-end and session boundaries. |
| Viewer | Shows candles, timestamp, controls, price, positions, entry and exit markers, stops, targets and event log, tested on a real replay. |

### Phase 2: Seven strategy engines

| Area | Criterion |
|---|---|
| Each of the seven | Defined inputs and outputs, config, hypotheses, signals, regime awareness, tests, and independent execution. |
| Validation | Chronological in-sample, validation and out-of-sample periods, with walk-forward. Out-of-sample data untouched during tuning, and each experiment logged. |
| Costs | All results shown under base and stressed cost scenarios. |
| Statistics | Confidence intervals, bootstrap, multiple-testing correction, parameter sensitivity, selection-bias analysis. |
| Reports | Reproducible from recorded dataset version, parameters and seed, with all metrics in Part III. |
| Honesty | Negative results are reported and retained. Data limitations for Strategies 3, 5 and 6 are explicit. |

### Phase 3: Risk and execution

| Area | Criterion |
|---|---|
| Independence | Tests prove no order path bypasses the risk engine, and that it keeps working when ML or strategies fail. |
| Limits | Each limit in Section 3.1 of the specification has passing and blocking tests. Emergency stop tested. |
| Exits | Risk-reducing exits are not trapped by entry limits. |
| MT5 | All listed functions covered. Mock-tested and real-terminal-tested functions are clearly distinguished. |
| Modes | All five exist. Live is off by default and needs explicit activation. Restart cannot silently enable it. |
| Security | No credentials in frontend, logs, Git or plaintext config, confirmed by a scan. |
| Recovery | Disconnect, reconnect, restart and state reconciliation are tested. |
| Paper trading | Works end to end, with a journal matching simulated fills. |

### Phase 4A: Skills and market intelligence

| Area | Criterion |
|---|---|
| Skills | All eleven are reachable through a common interface, each with a fidelity record: kept, adapted, or unavailable. |
| Fidelity | Upstream's documented examples and offline fixtures reproduce wherever logic is unchanged. Adaptations are listed one by one. |
| Licence | MIT notices retained. |
| Approval | `SKILL.md` steps that protect the user exist as code, not just text. |
| Outcome Simulator | Original verdict logic reproduced on upstream's fixture, then adapted to XAUUSD. Odds are compared with real outcomes. |
| Data adapters | Provider-independent, storing event, publication, ingestion and revision times. Each failure mode tested. |
| News | Optional. The platform runs without it. Coverage, licensing and timestamp reliability documented. Nothing fabricated. |

### Phase 4B: ML and knowledge

| Area | Criterion |
|---|---|
| Baselines | Statistical and tree baselines come first. Deep models are kept only with measured out-of-sample value. |
| Validation | Chronological and walk-forward, with purging where labels overlap. Leakage tests pass. |
| Registry | Model, data version, features, parameters and metrics are reproducible. |
| Inference | Reports confidence, abstains out of domain, detects drift. Cannot bypass risk, proved by test. |
| Knowledge | Retrieval returns attributed, timestamped sources and separates facts, findings, predictions and hypotheses. |
| Orchestration | Time Machine runs skill, strategy and ML combinations, passes the look-ahead test, and can reconstruct each decision. |

### Phase 5: Dashboard

| Area | Criterion |
|---|---|
| Sections | All ten exist and use real backend services. |
| Integrity | No hardcoded statistics or dead controls, confirmed by end-to-end tests. |
| Quality | Type checks and production build pass. Loading, error and responsive states exist. |
| Workflows | End-to-end tests cover replay, strategy run, risk rejection, paper order and emergency stop. |
| Safety | Live trading cannot be turned on by accident. No credentials reach the browser. |

---

## 15. Decisions needed from the project owner

| ID | Decision | Recommendation | Reason |
|---|---|---|---|
| **D1** | The tick download stopped on its own. Restart it now, or wait? | **Do not restart it until the month-end fix is in** (Phase 1, step 1.1). | Each month saved would lack its last day and use limited disk. Finished files stay valid apart from that day. |
| D2 | Which version of the six dual-version skills? | **v2** for News, Risk, Budget, Broker, Alerts and Analyst | XAUUSD trading matches v2's single-trade, stop-based model. v2 also keeps v1's weights mode. |
| D3 | How should automated mode relate to upstream's "never without your typed yes"? | Keep the rule in Assisted mode. Allow Automated only under Phase 3's explicit activation and risk engine, recorded as a deviation. | Your specification requires both, and they conflict. |
| D4 | Add short selling to the long-only skills? | **Yes**, recorded as an adaptation | XAUUSD is traded in both directions |
| D5 | Should Phase 1 add a minimal cross-market set? | **Yes:** DXY or EURUSD, silver and yields | Otherwise Strategy 3 cannot be fully done in Phase 2 |

---

## 16. How to run what exists today

Run from the repository root in Git Bash:

```bash
source venv/Scripts/activate
python -m pip install -r requirements.txt   # pandas, pyarrow, duckdb, tzdata
```

**Tests:**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests -v
```

**Read-only queries:**

```bash
PYTHONPATH=src python -m quantresearch.research_cli summary
PYTHONPATH=src python -m quantresearch.research_cli coverage
PYTHONPATH=src python -m quantresearch.research_cli query --timeframe m1 --start 2025-03-01 --end 2025-03-02 --limit 20
```

**These write outputs.** The first has the month-end defect, and the last must not be run:

```bash
# Downloader: loses the last day of each month until fixed
PYTHONPATH=src python -m quantresearch.dukascopy_download --timeframe m1 --price bid --start 2025-01-01 --end 2025-01-31

PYTHONPATH=src python -m quantresearch.coverage_audit
PYTHONPATH=src python -m quantresearch.raw_validator
PYTHONPATH=src python -m quantresearch.data_quality
PYTHONPATH=src python -m quantresearch.quality_pipeline
PYTHONPATH=src python -m quantresearch.parquet_audit
PYTHONPATH=src python -m quantresearch.feature_engineering --timeframes m1 --sides bid   # refuses if v1 exists
PYTHONPATH=src python -m quantresearch.data_catalog
PYTHONPATH=src python -m quantresearch.data_manifest

# Do not run: see Section 4.3
# PYTHONPATH=src python -m quantresearch.consolidate --source data/raw/xauusd/m1 --timeframe m1
```

**First-generation single-file tools:**

```bash
PYTHONPATH=src python -m quantresearch.ingestion data/raw/xauusd.csv --symbol XAUUSD \
  --timeframe 1m --assumed-timezone UTC \
  --output data/processed/xauusd_1m.csv --report data/processed/xauusd_audit.json

PYTHONPATH=src python -m quantresearch.session_audit data/processed/xauusd_1m.csv \
  --output data/processed/xauusd_session_audit.json

PYTHONPATH=src python -c "from quantresearch.market_data import *; save_candles(generate_candles('XAUUSD', 500), 'data/synthetic/xauusd_1m.csv')"
```

Use `--assumed-timezone Africa/Nairobi` if naive timestamps are in Nairobi time.
Timestamps with offsets are converted using their offset. The importer does not invent
candles or repair rows.

**Existing limits carried over from earlier notes:** the coverage audit recognises only the
monthly filename pattern, such as `xauusd_bid_m1_2025_03.csv`, and the candle validator
should not be used on tick files. Do not process a timeframe while its downloader is writing.
Do not commit licensed or restricted datasets.

---

## 17. Engineering principles

1. **Data integrity first:** preserve source files and record quality issues.
2. **Reproducibility:** deterministic tests, explicit configuration, logged experiment settings.
3. **No look-ahead bias:** use only information available at each simulated decision.
4. **Realistic execution:** account for spread, commission, slippage, contract specs and sessions.
5. **Out-of-sample validation:** separate research, validation and holdout periods.
6. **Risk before automation:** validate backtesting and paper trading before any live execution.
7. **Evidence-based progress:** report results and limits. Synthetic data and unvalidated backtests are not evidence of profitability.
8. **Passing checks are not proof of correct data:** a validator only proves what it tests, as Section 6.1 shows.

---

## 18. Progress log

### 2026-11-01: Phase 0 repository audit

- Audited source, tests, data, registries and both upstream skill repositories.
- Found and traced the month-end download defect.
- Confirmed observed bid and ask prices in sampled tick data, 2003 to 2010.
- Verified `day-trader-skill` and mapped all eleven skills plus Time Machine.
- Ran the test suite: 46 of 46 passed.
- Changed only this file. Earlier README content and `README_SETUP.md` were consolidated here.

### 2026-10-30 to 2026-10-31: Data pipeline (from files; uncommitted)

- Built the downloader, validators, Parquet pipeline, DuckDB layer and feature v1.
- Converted 2,420 files with 50,682,618 rows. Quarantined 119 rows.
- Built features for M1 to MN1: 10,328,016 rows.

### 2026-10-29: Session-aware data-quality audit

- Added gap classification against regular XAUUSD weekly hours, using New York time.
- On one month: 23 gaps, of which 20 were expected closures, 2 unexpected and 1 mixed, with
  27 potentially unexpected missing minutes.

### 2026-10-29: Historical ingestion layer

- Added column aliases, UTC normalisation, row validation, duplicate and order auditing, gap
  reporting, normalized CSV and JSON reports.

### 2026-10-29: Initial market-data layer

- Created the candle schema and reproducible synthetic candles, with validation and tests.
