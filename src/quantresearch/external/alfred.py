"""First-release US data from ALFRED (the St. Louis Fed's archive of every published vintage). No key needed.

    python -m quantresearch.external.alfred            # download or update, then write the events
    python -m quantresearch.external.alfred --offline  # rebuild the events from the cached vintages only

FRED shows today's revised numbers, which nobody knew on the day. A strategy that reacts to payrolls or CPI has to
see what was printed at the time. For each series, ALFRED lists every vintage date (each release, and the odd
correction). For each vintage, the newest observation is that period's **first release**, and the previous
observation as printed then is its first revision.

Each vintage CSV is cached under external/alfred/<series>/<vintage>.csv and never fetched twice. Requests are
paced (one a second by default) because the archive is a public service.

Events written (series name: value; all published at the vintage date, 08:30 New York, the release time of both
the Employment Situation and the CPI):
  ALFRED_PAYEMS_CHG   first-release monthly change in nonfarm payrolls (thousands)
  ALFRED_PAYEMS_REV   revision to the previous month's change, in that release (thousands)
  ALFRED_UNRATE       first-release unemployment rate (%)
  ALFRED_CPI_MOM      first-release headline CPI change on the month (%)
  ALFRED_CORE_MOM     first-release core CPI (less food and energy) change on the month (%)
and for each of those a *_SURP series: the first release minus the median of the previous 12 first releases.

That surprise is a proxy. A real surprise is the release minus the economists' consensus forecast, and no free,
reliable archive of consensus forecasts exists (the commercial ones are Bloomberg and Refinitiv; the free
calendars are scraped, incomplete and change their history). So the proxy measures "unusual against its own recent
past", which is weaker, and that is stated wherever it is used.
"""
from __future__ import annotations

import argparse
import io
import re
import time
import urllib.error
from datetime import date

import pandas as pd

from quantresearch.core.config import Paths, paths
from quantresearch.external import sources as src
from quantresearch.external import store as ext

SERIES = {  # ALFRED id: (event name, transform)
    "PAYEMS": ("ALFRED_PAYEMS", "diff"),
    "UNRATE": ("ALFRED_UNRATE", "level"),
    "CPIAUCSL": ("ALFRED_CPI", "pct"),
    "CPILFESL": ("ALFRED_CORE", "pct"),
}
FIRST_VINTAGE = date(1999, 6, 1)
SURPRISE_WINDOW = 12


def vintage_dates(series: str, getter=src.http_get) -> list[date]:
    page = getter(f"https://alfred.stlouisfed.org/series/downloaddata?seid={series}").decode("utf-8", "replace")
    found = sorted({date.fromisoformat(d) for d in re.findall(r'<option value="(\d{4}-\d{2}-\d{2})"', page)})
    if not found:
        raise RuntimeError(f"ALFRED listed no vintages for {series}")
    return found


def vintage(series: str, vdate: date, p: Paths, getter=src.http_get, pause: float = 1.0) -> pd.DataFrame:
    folder = p.external / "alfred" / series
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{vdate.isoformat()}.csv"
    if not path.exists():
        start = date(vdate.year - 2, 1, 1).isoformat()  # Two years of observations is plenty for first release and revision.
        raw = getter(f"https://alfred.stlouisfed.org/graph/alfredgraph.csv?id={series}&vintage_date={vdate.isoformat()}&cosd={start}")
        path.write_bytes(raw)
        time.sleep(pause)
    frame = pd.read_csv(io.BytesIO(path.read_bytes()), na_values=["."])
    frame.columns = ["period", "value"]
    frame["period"] = pd.to_datetime(frame["period"])
    return frame.dropna()


def first_releases(series: str, p: Paths, getter=src.http_get, pause: float = 1.0, offline: bool = False) -> pd.DataFrame:
    """One row per release: vintage date, period, first-release value and transform, and the revision to the previous one."""
    if offline:
        folder = p.external / "alfred" / series
        dates = sorted(date.fromisoformat(f.stem) for f in folder.glob("*.csv")) if folder.exists() else []
    else:
        dates = [d for d in vintage_dates(series, getter) if d >= FIRST_VINTAGE]
    transform = SERIES[series][1]
    rows, seen = [], set()
    missing = 0
    for d in dates:
        try:
            v = vintage(series, d, p, getter, pause)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            missing += 1  # Listed but not served (seen once for CPIAUCSL in 2019): that release is skipped and counted.
            continue
        if len(v) < 3:
            continue
        newest = v["period"].iloc[-1]
        if newest in seen:
            continue  # A correction or benchmark revision without a new month: no first release here.
        seen.add(newest)
        x0, x1, x2 = v["value"].iloc[-1], v["value"].iloc[-2], v["value"].iloc[-3]
        if transform == "diff":
            first, prev = x0 - x1, x1 - x2
        elif transform == "pct":
            first, prev = (x0 / x1 - 1) * 100, (x1 / x2 - 1) * 100
        else:
            first, prev = x0, x1
        rows.append({"vintage": pd.Timestamp(d), "period": newest, "first": first, "prev_as_revised": prev})
    out = pd.DataFrame(rows)
    out.attrs["missing_vintages"] = missing
    if out.empty:
        return out
    out["prev_first"] = out["first"].shift(1)
    out["revision"] = out["prev_as_revised"] - out["prev_first"]
    out["surprise"] = out["first"] - out["first"].shift(1).rolling(SURPRISE_WINDOW, min_periods=SURPRISE_WINDOW).median()
    return out


def published_at(vintages: pd.Series) -> pd.Series:
    local = pd.to_datetime(vintages).dt.normalize() + pd.Timedelta(hours=8, minutes=30)
    return local.dt.tz_localize("America/New_York").dt.tz_convert("UTC")


def to_events(series: str, rel: pd.DataFrame) -> pd.DataFrame:
    name, transform = SERIES[series]
    pub = published_at(rel["vintage"])
    period = pd.to_datetime(rel["period"]).dt.tz_localize("UTC")
    level = {"diff": "_CHG", "pct": "_MOM", "level": ""}[transform]
    parts = [src._events(name + level, period, pub, rel["first"].to_numpy(), "alfred", "release_time"),
             src._events(name + "_SURP", period, pub, rel["surprise"].to_numpy(), "alfred", "release_time")]
    if transform == "diff":
        parts.append(src._events(name + "_REV", period, pub, rel["revision"].to_numpy(), "alfred", "release_time"))
    return pd.concat(parts, ignore_index=True).dropna(subset=["value"])


def refresh(p: Paths | None = None, getter=src.http_get, pause: float = 1.0, offline: bool = False) -> dict[str, str]:
    p = p or paths()
    status = {}
    for series in SERIES:
        try:
            rel = first_releases(series, p, getter, pause, offline)
            ext.write_events(f"alfred_{series}", to_events(series, rel), p)
            status[series] = (f"ok ({len(rel)} releases, {rel['vintage'].min().date()} to {rel['vintage'].max().date()}"
                              f", {rel.attrs.get('missing_vintages', 0)} listed vintages not served)")
        except Exception as exc:  # One failing series must not stop the others.
            status[series] = f"failed: {type(exc).__name__}: {exc}"
    return status


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--pause", type=float, default=1.0)
    args = ap.parse_args(argv)
    for k, v in refresh(offline=args.offline, pause=args.pause).items():
        print(k, v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
