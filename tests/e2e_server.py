"""Starts the real API and built dashboard on a throwaway synthetic warehouse, for browser tests.

Synthetic bars exercise the software only. They say nothing about markets. Using a temporary folder
means a test order can never touch the real paper account.

    python tests/e2e_server.py <port>
"""
from __future__ import annotations

import sys
import tempfile
from datetime import timedelta

import pandas as pd
import uvicorn

from quantresearch.api.server import create_app
from quantresearch.api.services import Services
from quantresearch.core.config import Paths
from quantresearch.external import sources as src, store as ext
from quantresearch.warehouse import build
from quantresearch.warehouse.store import synthetic_bars


def main(port: int) -> None:
    folder = tempfile.mkdtemp(prefix="qr_e2e_")
    p = Paths(__import__("pathlib").Path(folder))
    end = pd.Timestamp.now(tz="UTC").floor("D")
    m1 = synthetic_bars(60 * 24 * 60, "m1", start=str(end - pd.Timedelta(days=60))[:19], seed=2, vol=0.0002)[build.BAR_COLUMNS]
    for (year, month), part in m1.groupby([m1.ts.dt.year, m1.ts.dt.month]):
        build._write_tf(part, "m1", int(year), int(month), p)
    build.build_higher(p)
    ext.write_events("seed", pd.concat([
        src.parse_fred_csv(b"d,v\n2020-01-02,1.5\n2020-01-03,1.6\n", "DFII10", timedelta(days=1)),
        src.fomc_events([pd.Timestamp("2020-01-29")]),
    ]), p)
    uvicorn.run(create_app(Services(p)), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8123)
