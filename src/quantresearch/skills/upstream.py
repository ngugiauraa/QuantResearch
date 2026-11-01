"""Access to the original skill code, kept unmodified under skills/upstream/ with its MIT licence.

Sources:
  day_trader/  github.com/Jakeschincariol/day-trader-skill at 3d0f4ef (news, risk, budget, broker,
               alerts, analyst, scanner, entry, exit, timemachine)
  trader/      github.com/Jakeschincariol/trader-skill at 5189e79 (copycat, screener)
Copyright (c) 2026 Jake Schincariol.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from quantresearch.core.config import paths

BASE = Path(__file__).resolve().parent / "upstream"
LOCATION = {
    "news": ("day_trader", "news"), "risk": ("day_trader", "check"), "budget": ("day_trader", "size"),
    "broker": ("day_trader", "ticket"), "alerts": ("day_trader", "alerts"), "analyst": ("day_trader", "facts"),
    "scanner": ("day_trader", "scan"), "entry": ("day_trader", "entry"), "exit": ("day_trader", "exit"),
    "timemachine": ("day_trader", "timemachine"), "copycat": ("trader", "edgar13f"), "screener": ("trader", "screen"),
}


def folder(skill: str) -> Path:
    return BASE / LOCATION[skill][0] / f"trader-{skill}"


def tool_path(skill: str) -> Path:
    return folder(skill) / f"{LOCATION[skill][1]}.py"


def instructions(skill: str) -> str:
    return (folder(skill) / "SKILL.md").read_text(encoding="utf-8")


def load(skill: str, private: bool = False) -> ModuleType:
    """Import an original tool. `private=True` gives an isolated copy that may be re-parameterised."""
    name = f"qr_upstream_{skill}" + ("_private" if private else "")
    if not private and name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, tool_path(skill))
    module = importlib.util.module_from_spec(spec)
    if not private:
        sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def workspace() -> Path:
    """Upstream tools read and write a trader/ folder relative to their working directory."""
    root = paths().app / "skills"
    (root / "trader").mkdir(parents=True, exist_ok=True)
    return root


def run_tool(skill: str, args: list[str], timeout: int = 180, cwd: Path | None = None) -> dict:
    """Run an original tool as its own process, as its documentation describes."""
    env = {k: v for k, v in os.environ.items() if k in ("SYSTEMROOT", "PATH", "TEMP", "TMP", "SEC_USER_AGENT", "PYTHONUTF8")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        done = subprocess.run([sys.executable, str(tool_path(skill))] + args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, cwd=str(cwd or workspace()), env=env)
    except subprocess.TimeoutExpired:
        return {"returncode": None, "stdout": "", "stderr": f"Timed out after {timeout}s"}
    return {"returncode": done.returncode, "stdout": done.stdout, "stderr": done.stderr}


def write_workspace_json(name: str, data) -> Path:
    path = workspace() / "trader" / name
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return path
