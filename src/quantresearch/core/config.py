"""Project paths. Every data path can be redirected with QR_DATA_ROOT."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def warehouse(self) -> Path:
        return self.root / "warehouse"

    @property
    def bi5(self) -> Path:
        return self.warehouse / "bi5" / "xauusd"

    @property
    def bars(self) -> Path:
        return self.warehouse / "bars" / "xauusd"

    @property
    def quarantine(self) -> Path:
        return self.warehouse / "quarantine" / "xauusd"

    @property
    def manifest(self) -> Path:
        return self.warehouse / "manifest.sqlite"

    @property
    def tmp(self) -> Path:
        return self.warehouse / "tmp"

    @property
    def reports(self) -> Path:
        return self.warehouse / "reports"

    @property
    def external(self) -> Path:
        return self.warehouse / "external"

    @property
    def app(self) -> Path:
        return self.root / "app"

    @property
    def research(self) -> Path:
        return self.root / "research"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def replays(self) -> Path:
        return self.root / "replays"


def paths() -> Paths:
    return Paths(Path(os.environ.get("QR_DATA_ROOT", ROOT / "data")))
