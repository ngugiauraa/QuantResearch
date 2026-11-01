"""Adapters for free, legally accessible sources. Each returns the common event table.

What is honest about these timestamps:
  - FRED CSV gives only the latest revised values and no release times. Publication times here are
    conservative ASSUMED lags, marked in `pub_basis`. Revised macro levels are therefore
    usable for context only; true first-release values and surprises need vintage data
    (an ALFRED API key), so no macro-surprise feature is claimed.
  - COT report dates are Tuesdays, and CFTC publishes the following Friday at 15:30 New York.
  - FOMC decision times use the customary 14:00 New York statement time.
  - Cross-market bars come from the same provider as gold and are available at bar close.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from quantresearch.core.config import Paths, paths
from quantresearch.dukascopy_download import DOWNLOADER
from quantresearch.features.pit import EVENT_COLUMNS

USER_AGENT = "Mozilla/5.0 (compatible; QuantResearch/1.0)"
RETRIES = 3

# series: (description, assumed publication lag from observation date, kind)
FRED_SERIES = {
    "DFF": ("Effective federal funds rate", timedelta(days=1, hours=22), "rates"),
    "DGS2": ("2-year Treasury yield", timedelta(days=1, hours=22), "rates"),
    "DGS10": ("10-year Treasury yield", timedelta(days=1, hours=22), "rates"),
    "DFII10": ("10-year real yield (TIPS)", timedelta(days=1, hours=22), "rates"),
    "T10YIE": ("10-year breakeven inflation", timedelta(days=1, hours=22), "rates"),
    "DTWEXBGS": ("Broad trade-weighted dollar", timedelta(days=8), "fx"),
    "VIXCLS": ("VIX close", timedelta(days=1, hours=22), "risk"),
    "DCOILWTICO": ("WTI crude oil", timedelta(days=8), "commodity"),
    "CPIAUCSL": ("CPI, all urban, revised", timedelta(days=50), "macro"),
    "PCEPI": ("PCE price index, revised", timedelta(days=65), "macro"),
    "PAYEMS": ("Nonfarm payrolls, revised", timedelta(days=40), "macro"),
    "UNRATE": ("Unemployment rate, revised", timedelta(days=40), "macro"),
    "GDP": ("Nominal GDP, revised", timedelta(days=125), "macro"),
}

CROSS_SYMBOLS = {
    "xagusd": "Silver", "eurusd": "EURUSD", "gbpusd": "GBPUSD", "usdjpy": "USDJPY",
    "dollaridxusd": "Dollar index CFD", "usa500idxusd": "S&P 500 index CFD",
    "lightcmdusd": "WTI crude CFD", "ustbondtrusd": "US T-bond CFD",
}


# Some provider instruments start later. Requesting earlier dates makes the tool fail outright.
CROSS_START = {"usa500idxusd": "2012-01-01", "lightcmdusd": "2012-01-01"}


def http_get(url: str, timeout: int = 90) -> bytes:
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                raise  # A 403 or 404 will not change by asking again.
            last = exc
        except OSError as exc:
            last = exc
        time.sleep(3 * (attempt + 1))
    raise last


def _events(series, event_ts, published_ts, values, source, basis) -> pd.DataFrame:
    frame = pd.DataFrame({
        "series": series, "event_ts": event_ts, "published_ts": published_ts, "revision": 0,
        "value": values, "source": source,
    })
    frame["ingested_ts"] = pd.Timestamp(datetime.now(timezone.utc))
    frame["pub_basis"] = basis
    return frame[EVENT_COLUMNS + ["pub_basis"]]


def parse_fred_csv(raw: bytes, series: str, lag: timedelta) -> pd.DataFrame:
    frame = pd.read_csv(io.BytesIO(raw), na_values=["."])
    frame.columns = ["date", "value"]
    frame = frame.dropna(subset=["value"])
    event = pd.to_datetime(frame["date"], utc=True)
    return _events(series, event, event + lag, frame["value"].astype(float).to_numpy(), "fred", "assumed_lag")


def fetch_fred(series: str, getter=http_get) -> pd.DataFrame:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={urllib.parse.quote(series)}"
    return parse_fred_csv(getter(url), series, FRED_SERIES[series][1])


MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


def parse_fomc_historical(html: str, year: int) -> list[pd.Timestamp]:
    """Scheduled decision days from a Fed historical page. Unscheduled calls are left out."""
    found = []
    for match in re.finditer(rf"({MONTHS})\s+(\d{{1,2}})(?:-(\d{{1,2}}))?\s+Meeting\s*-\s*{year}", html):
        month, first, last = match.group(1), match.group(2), match.group(3)
        day = int(last or first)
        found.append(pd.Timestamp(f"{month} {day} {year}"))
    return sorted(set(found))


def parse_fomc_current(html: str) -> list[pd.Timestamp]:
    """Decision days from the current calendars page, which groups meetings by year panel."""
    out = []
    panels = re.split(r"(\d{4})\s+FOMC Meetings", html)
    for index in range(1, len(panels) - 1, 2):
        year = int(panels[index])
        pattern = rf'fomc-meeting__month[^>]*>\s*<strong>\s*({MONTHS})(?:/({MONTHS}))?\s*</strong>.*?fomc-meeting__date[^>]*>\s*(\d{{1,2}})(?:-(\d{{1,2}}))?'
        for m in re.finditer(pattern, panels[index + 1], re.S):
            month = m.group(2) or m.group(1)
            day = int(m.group(4) or m.group(3))
            out.append(pd.Timestamp(f"{month} {day} {year}"))
    return sorted(set(out))


def fomc_events(dates: list[pd.Timestamp]) -> pd.DataFrame:
    stamps = [d.tz_localize("America/New_York") + pd.Timedelta(hours=14) for d in dates]
    utc = pd.Series(stamps, dtype="datetime64[ns, America/New_York]").dt.tz_convert("UTC")
    return _events("FOMC_DECISION", utc, utc, 1.0, "federalreserve.gov", "customary_statement_time")


def fetch_fomc(start_year: int = 2003, getter=http_get) -> pd.DataFrame:
    dates: list[pd.Timestamp] = []
    current = parse_fomc_current(getter("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm").decode("utf-8", "replace"))
    dates += current
    first_current = min((d.year for d in current), default=datetime.now().year)
    for year in range(start_year, first_current):
        html = getter(f"https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm").decode("utf-8", "replace")
        dates += parse_fomc_historical(html, year)
    return fomc_events(sorted(set(dates)))


def parse_cot(raw_zip: bytes, market: str = "GOLD - COMMODITY EXCHANGE INC.") -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as archive:
        with archive.open(archive.namelist()[0]) as handle:
            frame = pd.read_csv(handle, low_memory=False)
    frame.columns = [c.strip() for c in frame.columns]
    rows = frame[frame["Market_and_Exchange_Names"].str.strip() == market].copy()
    date_col = "Report_Date_as_YYYY-MM-DD" if "Report_Date_as_YYYY-MM-DD" in rows else "Report_Date_as_MM_DD_YYYY"
    report = pd.to_datetime(rows[date_col])
    # Published the following Friday at 15:30 New York.
    published = (report + pd.Timedelta(days=3, hours=15, minutes=30)).dt.tz_localize("America/New_York").dt.tz_convert("UTC")
    event = report.dt.tz_localize("UTC")
    fields = {
        "COT_MM_NET": rows["M_Money_Positions_Long_All"] - rows["M_Money_Positions_Short_All"],
        "COT_PROD_NET": rows["Prod_Merc_Positions_Long_All"] - rows["Prod_Merc_Positions_Short_All"],
        "COT_SWAP_NET": rows["Swap_Positions_Long_All"] - rows["Swap__Positions_Short_All"],
        "COT_OI": rows["Open_Interest_All"],
    }
    return pd.concat([_events(name, event, published, values.astype(float).to_numpy(), "cftc", "published_schedule")
                      for name, values in fields.items()], ignore_index=True)


def fetch_cot(start_year: int = 2017, end_year: int | None = None, getter=http_get) -> pd.DataFrame:
    """CFTC's disaggregated history: one file for 2006-2016, then one per year. A missing year is skipped."""
    frames = [parse_cot(getter("https://www.cftc.gov/files/dea/history/fut_disagg_txt_hist_2006_2016.zip", 120))]
    end_year = end_year or datetime.now().year
    for year in range(start_year, end_year + 1):
        try:
            frames.append(parse_cot(getter(f"https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip", 120)))
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
    return pd.concat(frames, ignore_index=True).drop_duplicates(["series", "event_ts"], keep="last")


def fetch_cross(symbol: str, timeframe: str = "h1", start: str = "2003-05-01", end: str | None = None,
                p: Paths | None = None) -> Path:
    p = p or paths()
    end = end or datetime.now(timezone.utc).date().isoformat()
    npx = shutil.which("npx") or "npx"
    with tempfile.TemporaryDirectory() as folder:
        done = subprocess.run([npx, "--yes", DOWNLOADER, "-i", symbol, "-from", start, "-to", end, "-t", timeframe, "-p", "bid",
                               "-v", "-vu", "units", "-f", "csv", "-df", "iso", "-bs", "8", "-bp", "500",
                               "-dir", folder, "-fn", "out"], capture_output=True, text=True, errors="replace", timeout=1800)
        if done.returncode != 0 or not (Path(folder) / "out.csv").exists():
            raise RuntimeError(f"{symbol}: {(done.stdout + done.stderr)[-300:]}")
        frame = pd.read_csv(Path(folder) / "out.csv")
    frame["ts"] = pd.to_datetime(frame.pop("timestamp"), utc=True)
    target = p.external / "cross" / f"{symbol}_{timeframe}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    frame[["ts", "open", "high", "low", "close", "volume"]].to_parquet(target, index=False)
    return target


def fetch_news_gdelt(query: str = "gold price OR federal reserve", max_records: int = 250, getter=http_get) -> pd.DataFrame:
    """Recent headlines. Optional input: coverage is partial and starts in 2017 at the earliest."""
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?format=json&mode=artlist&sort=datedesc"
           f"&maxrecords={max_records}&query={urllib.parse.quote(query)}")
    return parse_gdelt(getter(url))


def parse_gdelt(raw: bytes) -> pd.DataFrame:
    articles = json.loads(raw).get("articles", [])
    if not articles:
        return pd.DataFrame(columns=["published_ts", "ingested_ts", "title", "url", "domain", "source", "language"])
    frame = pd.DataFrame(articles)
    return pd.DataFrame({
        "published_ts": pd.to_datetime(frame["seendate"], format="%Y%m%dT%H%M%SZ", utc=True),
        "ingested_ts": pd.Timestamp(datetime.now(timezone.utc)),
        "title": frame["title"], "url": frame["url"], "domain": frame["domain"],
        "source": "gdelt", "language": frame.get("language"),
    })
