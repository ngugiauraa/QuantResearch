"""Train and register a direction model on data before a cutoff.

    python -m quantresearch.ml.train_cli --tf h1 --end 2024-10-30
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from quantresearch.features.registry import build_features
from quantresearch.ml import pipeline as ml
from quantresearch.research.experiment import dataset_version
from quantresearch.warehouse.store import load_bars


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tf", default="h1")
    ap.add_argument("--end", default=None, help="Train only on bars opening before this date.")
    ap.add_argument("--horizon", type=int, default=24)
    args = ap.parse_args()
    frame = build_features(load_bars(args.tf, None, args.end))
    result = ml.train(frame, ml.ModelRegistry(), dataset_version(), horizon=args.horizon)
    result.pop("model")
    print(json.dumps(result, indent=1, default=lambda v: float(v) if isinstance(v, np.floating) else str(v)))


if __name__ == "__main__":
    main()
