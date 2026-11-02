"""The autonomous research controller.

    python -m quantresearch.discovery.controller run --run-id main --hours 48
    python -m quantresearch.discovery.controller supervise --run-id main --hours 48   # restarts after a crash
    python -m quantresearch.discovery.controller status|stop|report --run-id main

How a run proceeds, in synchronous rounds:
  1. The scheduler picks search tasks (method x timeframe x vintage x dataset) with a bandit whose reward comes
     ONLY from training-window outcomes. Out-of-sample results never steer the search.
  2. Candidates frozen in earlier rounds get out-of-sample, robustness and confirmation tasks.
  3. All tasks run in parallel worker processes. Results are applied in task order, so the outcome does not
     depend on worker count or completion order. Seeds derive from (run seed, round, task position).
  4. Statistical gates are recomputed over every out-of-sample look so far, ranking is refreshed, and a verified
     checkpoint is written. A crash loses at most the current round, which is re-run identically on resume.
Stops at the time or experiment limit, on a STOP file or Ctrl+C (after finishing the round), when every search
cell is exhausted, or on futility after a minimum runtime. The discovery package cannot place orders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing as mp
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from quantresearch.core.config import Paths, paths
from quantresearch.discovery import generate as G
from quantresearch.discovery import lab
from quantresearch.discovery import ranking
from quantresearch.discovery import spec as S
from quantresearch.discovery.config import HOLDOUT_NOTE, DiscoveryConfig, run_dir, store_dir

KINDS = ("gp", "template", "miner", "symreg", "random")
FAMILY_FREEZE_CAP = 2
KIND_PRIOR = {"gp": 0.35, "template": 0.25, "miner": 0.15, "symreg": 0.10, "random": 0.15}
CHECK_EXPERIMENTS = 0


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def derive_seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def cid_of(dataset: str, vintage: str, spec_hash: str) -> str:
    return hashlib.sha256(f"{dataset}|{vintage}|{spec_hash}".encode()).hexdigest()[:20]


def structural_family(spec: dict) -> str:
    """Spec with every number blanked: candidates differing only in constants share a family."""
    s = S.normalize(spec)
    s.pop("meta", None)

    def blank(x):
        if isinstance(x, dict):
            return {k: blank(v) for k, v in x.items()}
        if isinstance(x, list):
            return [blank(v) for v in x]
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            return "#"
        return x
    return hashlib.sha256(S.canonical(blank(s)).encode()).hexdigest()[:12]


def code_version() -> str:
    try:
        root = Path(__file__).resolve().parents[3]
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "src"], cwd=root, capture_output=True, text=True, timeout=10).stdout.strip()
        return head + ("+uncommitted" if dirty else "")
    except Exception:
        return "unknown"


# ---------------------------------------------------------------- worker side
def _worker_init(parent_pid: int | None = None) -> None:
    try:
        import psutil
        p = psutil.Process()
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if sys.platform == "win32" else 10)
    except Exception:
        pass
    if parent_pid:
        # On Windows a spawned worker does not notice its controller dying, and would live on holding memory.
        # Each worker therefore exits by itself shortly after its parent disappears.
        def watch():
            import psutil
            while True:
                time.sleep(5)
                if not psutil.pid_exists(parent_pid):
                    os._exit(0)
        threading.Thread(target=watch, daemon=True).start()


def execute(task: dict) -> dict:
    """Run one task in a worker. Errors come back as data, never as a dead pool."""
    try:
        kind = task["kind"]
        if kind in ("gp", "template", "miner", "symreg", "random", "proposal"):
            from quantresearch.discovery.search import run_search
            out = run_search(task)
        elif kind == "oos":
            from quantresearch.discovery.validate import run_oos
            out = run_oos(task)
        elif kind == "robust":
            from quantresearch.discovery.validate import run_robust
            out = run_robust(task)
        elif kind == "confirm":
            from quantresearch.discovery.validate import run_confirm
            out = run_confirm(task)
        elif kind == "benchmarks":
            from quantresearch.discovery.benchmarks import compute
            out = compute(task)
        else:
            raise ValueError(f"unknown task kind {kind}")
        out["ok"] = True
    except Exception as exc:
        out = {"task_id": task["task_id"], "ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-3000:]}
    try:
        import psutil
        mi = psutil.Process().memory_info()
        out["peak_rss_mb"] = getattr(mi, "peak_wset", mi.rss) / 1e6
    except Exception:
        out["peak_rss_mb"] = None
    return out


# ---------------------------------------------------------------- resources
class Monitor(threading.Thread):
    def __init__(self, interval: float = 5.0):
        super().__init__(daemon=True)
        self.interval = interval
        self.samples: list[dict] = []
        self.peak_rss = 0.0
        self.alive = True
        self.lock = threading.Lock()
        self.procs: dict = {}
        self.last: dict = {}
        self.suspended_s = 0.0
        self.suspensions: list[tuple[float, float]] = []
        self._tick = time.time()

    def sample(self) -> dict:
        import psutil
        me = psutil.Process()
        live = [me] + me.children(recursive=True)
        # cpu_percent measures since the previous call on the SAME object, so handles are kept between samples.
        for p in live:
            self.procs.setdefault(p.pid, p)
        cpu = rss = 0.0
        procs = []
        for pid, p in list(self.procs.items()):
            try:
                if not p.is_running():
                    raise psutil.NoSuchProcess(pid)
                cpu += p.cpu_percent(None)
                rss += p.memory_info().rss
                procs.append(p)
            except Exception:
                self.procs.pop(pid, None)
        vm = psutil.virtual_memory()
        return {"cpu_pct": cpu, "rss_mb": rss / 1e6, "sys_avail_mb": vm.available / 1e6, "workers": len(procs) - 1}

    def run(self) -> None:
        while self.alive:
            now_t = time.time()
            gap = now_t - self._tick
            if gap > max(60.0, 6 * self.interval):
                # The thread sleeps for `interval`; a much longer gap means the whole machine was suspended.
                with self.lock:
                    self.suspended_s += gap - self.interval
                    self.suspensions.append((self._tick, now_t))
            self._tick = now_t
            try:
                s = self.sample()
                with self.lock:
                    self.samples.append(s)
                    self.peak_rss = max(self.peak_rss, s["rss_mb"])
            except Exception:
                pass
            time.sleep(self.interval)

    def drain(self) -> dict:
        with self.lock:
            xs, self.samples = self.samples, []
        if not xs:
            xs = [self.last] if self.last else []
        if not xs:
            return {}
        self.last = xs[-1]
        return {"cpu_pct": float(np.mean([x["cpu_pct"] for x in xs])), "rss_mb": float(np.mean([x["rss_mb"] for x in xs])),
                "sys_avail_mb": float(min(x["sys_avail_mb"] for x in xs)), "workers": int(max(x["workers"] for x in xs)), "peak_rss_mb": self.peak_rss}


def keep_awake(on: bool) -> None:
    """Stop Windows sleeping during a long run (the setting is released when the process ends)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        flags = 0x80000000 | (0x00000001 if on else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        pass


def default_workers(cfg: DiscoveryConfig) -> int:
    import psutil
    cores = psutil.cpu_count(logical=False) or 2
    avail_gb = psutil.virtual_memory().available / 1e9
    by_mem = int(max(1, (avail_gb - cfg.min_free_memory_gb) / 0.35))
    return max(1, min(cores - 1, by_mem, 5))


# ---------------------------------------------------------------- controller
class Controller:
    def __init__(self, cfg: DiscoveryConfig, p: Paths | None = None, resume: bool = False, log=print):
        self.cfg, self.p, self.log_fn = cfg, p or paths(), log
        self.dir = run_dir(cfg.run_id, self.p)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db = lab.connect(self.dir / "lab.sqlite")
        self.stop_requested = False
        self.in_round = False
        self.event_buffer: list = []
        self.session_start = time.time()
        self.vintages = {v.key: v for v in cfg.vintage_objects()}
        self.real_root = Path(cfg.store_dir) if cfg.store_dir else store_dir(self.p, "real")
        self.null_root = (Path(cfg.store_dir).parent / f"null_{cfg.seed % 1000}") if cfg.store_dir else store_dir(self.p, f"null_{cfg.seed % 1000}")
        self.state = self._init_state(resume)
        self.workers = cfg.workers or default_workers(cfg)
        self.pool: ProcessPoolExecutor | None = None
        self.monitor = Monitor()
        self.benchmarks = None

    # ------------------------------------------------------------ bookkeeping
    def log(self, message: str, level: str = "info") -> None:
        row = (self.cfg.run_id, now(), level, message)
        if getattr(self, "in_round", False):
            self.event_buffer.append(row)  # written inside the round's transaction
        else:
            self.db.execute("INSERT INTO events(run_id, ts, level, message) VALUES(?,?,?,?)", row)
            self.db.commit()
        line = f"{now()[:19]} [{level}] {message}"
        self.log_fn(line)
        with open(self.dir / "research.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def _init_state(self, resume: bool) -> dict:
        row = self.db.execute("SELECT * FROM runs WHERE run_id=?", (self.cfg.run_id,)).fetchone()
        if row is not None and not resume:
            raise SystemExit(f"Run {self.cfg.run_id} exists. Use --resume to continue it, or choose another --run-id.")
        if row is None:
            self.db.execute("INSERT INTO runs(run_id, created_at, config, fingerprint, status, started_at, code_version) VALUES(?,?,?,?,?,?,?)",
                            (self.cfg.run_id, now(), json.dumps(self.cfg.__dict__, default=str), self.cfg.fingerprint(), "starting", now(), code_version()))
            self.db.commit()
            return {"round": 0, "elapsed_s": 0.0, "cells": {}, "feature_weights": {}, "template_stats": {}, "last_wf_round": 0, "experiments": 0}
        if row["fingerprint"] != self.cfg.fingerprint():
            raise SystemExit("The configuration differs from the run being resumed; results would not be reproducible. "
                             f"Saved {row['fingerprint']}, now {self.cfg.fingerprint()}.")
        state = self.load_checkpoint()
        # A round's results and its checkpoint commit in one transaction, so an interrupted round left nothing
        # behind; it is simply recomputed, with the same seeds, from this checkpoint.
        stray = self.db.execute("SELECT count(*) FROM rounds WHERE run_id=? AND round>?", (self.cfg.run_id, state["round"])).fetchone()[0]
        if stray:
            raise SystemExit(f"Database has {stray} round(s) after checkpoint {state['round']}: inconsistent state, refusing to resume.")
        if not state.get("sleep_audit_v2"):
            # Rounds recorded before sleep detection existed: time with no work happening, inside a round (wall time
            # far above the slowest task's CPU time) or between two rounds (a long pause before the next started),
            # is a suspended computer. It is removed from research time once; later sleeps are caught live.
            gap = 0.0
            prev_end = None
            for r in self.db.execute("SELECT r.round, julianday(r.started_at)*86400 AS s, julianday(r.ended_at)*86400 AS e, max(t.cpu_s) AS cpu "
                                     "FROM rounds r LEFT JOIN tasks t ON t.run_id=r.run_id AND t.round=r.round WHERE r.run_id=? "
                                     "GROUP BY r.round ORDER BY r.round", (self.cfg.run_id,)):
                wall = (r["e"] or 0) - (r["s"] or 0)
                if wall > 1800 and (r["cpu"] or 0) < 0.25 * wall:
                    gap += wall - max(r["cpu"] or 0, 60.0)
                if prev_end is not None and r["s"] - prev_end > 900:
                    gap += r["s"] - prev_end - 60.0
                prev_end = r["e"]
            sessions = self.db.execute("SELECT count(*) FROM events WHERE run_id=? AND message LIKE 'resumed from%'", (self.cfg.run_id,)).fetchone()[0]
            if sessions:  # pauses between a stop and the following resume are not research time either, and never were counted
                stops = [r[0] for r in self.db.execute("SELECT julianday(ts)*86400 FROM events WHERE run_id=? AND message LIKE 'stopping:%' ORDER BY id", (self.cfg.run_id,))]
                resumes = [r[0] for r in self.db.execute("SELECT julianday(ts)*86400 FROM events WHERE run_id=? AND message LIKE 'resumed from%' ORDER BY id", (self.cfg.run_id,))]
                for a_, b_ in zip(stops, resumes):
                    if b_ - a_ > 900:  # such a pause was added above as a between-round gap, but was never counted
                        gap -= b_ - a_ - 60.0
            gap = max(0.0, gap)
            state["suspended_s"] = float(state.get("suspended_s", 0.0)) + gap
            state["elapsed_s"] = max(0.0, float(state.get("elapsed_s", 0.0)) - gap)
            state["sleep_audit_v2"] = True
            self._retro_suspended = gap
        self.db.execute("UPDATE runs SET sessions=sessions+1, status='resumed' WHERE run_id=?", (self.cfg.run_id,))
        self.db.commit()
        self._resumed_at = state["round"]
        return state

    def checkpoint(self) -> None:
        """Write and verify the state file, record it, and commit everything for the round in one transaction."""
        self.state["elapsed_s"] = self.elapsed()
        self.state["suspended_s"] = self.suspended()
        while self.monitor.suspensions:
            a, b = self.monitor.suspensions.pop(0)
            self.log(f"computer was asleep for {(b - a) / 60:.0f} min ({datetime.fromtimestamp(a, timezone.utc).isoformat()[:19]} to "
                     f"{datetime.fromtimestamp(b, timezone.utc).isoformat()[:19]} UTC); not counted as research time", "warn")
            if self.in_round and self.event_buffer:
                self.db.executemany("INSERT INTO events(run_id, ts, level, message) VALUES(?,?,?,?)", self.event_buffer)
                self.event_buffer = []
        path = self.dir / "checkpoints" / f"round_{self.state['round']:06d}.json"
        path.parent.mkdir(exist_ok=True)
        body = json.dumps(lab.jsonable(self.state), sort_keys=True)
        digest = hashlib.sha256(body.encode()).hexdigest()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, path)
        if hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest() != digest:
            raise RuntimeError("checkpoint verification failed")
        self.db.execute("INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?,?)", (self.cfg.run_id, self.state["round"], str(path), digest, now()))
        if self.event_buffer:
            self.db.executemany("INSERT INTO events(run_id, ts, level, message) VALUES(?,?,?,?)", self.event_buffer)
            self.event_buffer = []
        self.db.commit()
        old = sorted(path.parent.glob("round_*.json"))[:-5]
        for f in old:  # older checkpoints are superseded by the database rows; the last five are kept
            f.unlink(missing_ok=True)

    def load_checkpoint(self) -> dict:
        rows = self.db.execute("SELECT * FROM checkpoints WHERE run_id=? ORDER BY round DESC", (self.cfg.run_id,)).fetchall()
        for row in rows:
            path = Path(row["path"])
            if path.exists():
                body = path.read_text(encoding="utf-8")
                if hashlib.sha256(body.encode()).hexdigest() == row["sha256"]:
                    return json.loads(body)
                self.log(f"checkpoint {path.name} failed verification; trying an older one", "warn")
        return {"round": 0, "elapsed_s": 0.0, "cells": {}, "feature_weights": {}, "template_stats": {}, "last_wf_round": 0, "experiments": 0}

    # ------------------------------------------------------------ stores
    def ensure_stores(self) -> None:
        from quantresearch.discovery.store import build
        for root, dataset in ((self.real_root, "real"), (self.null_root, "null")):
            if dataset == "null" and self.cfg.null_share <= 0:
                continue
            missing = [tf for tf in self.cfg.timeframes if not (root / tf / "meta.json").exists()]
            if missing:
                self.log(f"building {dataset} feature store for {missing}")
                build(dataset, tuple(missing), self.p, seed=self.cfg.seed % 1000, out_root=root, log=lambda m: self.log(m))
        datasets = {}
        for root, dataset in ((self.real_root, "real"), (self.null_root, "null")):
            for tf in self.cfg.timeframes:
                meta_path = root / tf / "meta.json"
                if meta_path.exists():
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    datasets[f"{dataset}/{tf}"] = {"bars": meta["bars"], "last_close": meta["last_close"], "sha256": meta["sha256"]["features.npy"][:16]}
                    if meta["last_close"] > meta["holdout_start"]:
                        raise SystemExit(f"{meta_path} reaches the holdout")
        self.db.execute("UPDATE runs SET datasets=? WHERE run_id=?", (json.dumps(datasets), self.cfg.run_id))
        self.db.commit()

    def store_names(self, dataset: str, tf: str) -> list[str]:
        root = self.real_root if dataset == "real" else self.null_root
        return json.loads((root / tf / "meta.json").read_text(encoding="utf-8"))["features"]

    # ------------------------------------------------------------ pool
    def start_pool(self) -> None:
        for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMBA_NUM_THREADS"):
            os.environ[var] = "1"
        # No max_tasks_per_child: with spawn it can deadlock when a worker retires (observed here). Memory growth is
        # capped instead by recycling the whole pool every `worker_task_limit` rounds.
        self.pool = ProcessPoolExecutor(max_workers=self.workers, mp_context=mp.get_context("spawn"), initializer=_worker_init,
                                        initargs=(os.getpid(),))
        self.pool_started_round = self.state["round"]

    def restart_pool(self, workers: int | None = None, kill: bool = False) -> None:
        if self.pool is not None and not kill:
            self.pool.shutdown(wait=True, cancel_futures=True)
            self.pool = None
        if self.pool is not None:
            try:
                for proc in list(getattr(self.pool, "_processes", {}).values()):
                    proc.kill()
            except Exception:
                pass
            self.pool.shutdown(wait=False, cancel_futures=True)
        if workers:
            self.workers = workers
        self.start_pool()

    # ------------------------------------------------------------ scheduling
    def cell_key(self, dataset, tf, vintage, kind) -> str:
        return f"{dataset}|{tf}|{vintage}|{kind}"

    def cells(self) -> list[str]:
        out = []
        for dataset in (("real", "null") if self.cfg.null_share > 0 else ("real",)):
            for tf in self.cfg.timeframes:
                for v in self.vintages:
                    for kind in KINDS:
                        out.append(self.cell_key(dataset, tf, v, kind))
        return out

    def cell_stats(self, key: str) -> dict:
        return self.state["cells"].setdefault(key, {"tasks": 0, "evaluated": 0, "screen_passed": 0, "selected": 0, "novelty": [], "retired": False,
                                                    "recent_selected": []})

    def live_cells(self, dataset: str) -> list[str]:
        return [c for c in self.cells() if c.startswith(dataset + "|") and not self.cell_stats(c)["retired"]]

    def pick_cells(self, rng: np.random.Generator, n: int) -> list[str]:
        chosen = []
        total = sum(self.cell_stats(c)["tasks"] for c in self.cells()) + 1
        for i in range(n):
            dataset = "null" if (self.cfg.null_share > 0 and rng.random() < self.cfg.null_share) else "real"
            live = self.live_cells(dataset) or self.live_cells("real" if dataset == "null" else "null")
            if not live:
                break
            scores = []
            for c in live:
                st = self.cell_stats(c)
                kind = c.split("|")[3]
                mean = (st["selected"] + 0.2 * st["screen_passed"] / max(1, st["evaluated"])) / max(1, st["tasks"])
                bonus = math.sqrt(2 * math.log(total) / (st["tasks"] + 1))
                penalty = 0.5 if c in chosen else 0.0
                scores.append(mean + bonus + KIND_PRIOR[kind] + 0.01 * rng.random() - penalty)
            chosen.append(live[int(np.argmax(scores))])
        return chosen

    def feature_subset(self, dataset: str, tf: str, rng: np.random.Generator) -> list[str] | None:
        """Genetic feature selection: subsets drawn by weights that grow with training-window success."""
        if rng.random() < 0.4:
            return None
        names = sorted(self.store_names(dataset, tf))
        w = self.state["feature_weights"].setdefault(f"{dataset}|{tf}", {})
        weights = np.array([w.get(n, 1.0) for n in names], float)
        k = int(min(len(names), rng.integers(25, 45)))
        idx = rng.choice(len(names), size=k, replace=False, p=weights / weights.sum())
        return sorted(names[int(i)] for i in idx)

    def archive_seeds(self, dataset: str, tf: str, vintage: str, rng: np.random.Generator) -> list[dict]:
        """Seeds from this cell or from EARLIER vintages only (their data ended earlier, so nothing leaks)."""
        rows = self.db.execute("SELECT cell, payload FROM archives WHERE run_id=?", (self.cfg.run_id,)).fetchall()
        pool = []
        for r in rows:
            d, t, v, _ = r["cell"].split("|")
            if d == dataset and t == tf and v <= vintage:
                pool.extend(json.loads(r["payload"]))
        pool.sort(key=S.canonical)
        if not pool:
            return []
        idx = rng.choice(len(pool), size=min(6, len(pool)), replace=False)
        return [pool[int(i)] for i in sorted(idx)]

    def search_task(self, cell: str, rnd: int, seq: int) -> dict:
        dataset, tf, vintage, kind = cell.split("|")
        seed = derive_seed(self.cfg.seed, rnd, seq, cell)
        rng = np.random.default_rng(seed)
        params = {"selected_per_task": self.cfg.selected_per_task, "features": self.feature_subset(dataset, tf, rng)}
        if kind == "gp":
            params.update(population=self.cfg.gp_population, generations=self.cfg.gp_generations, seeds=self.archive_seeds(dataset, tf, vintage, rng))
        elif kind == "template":
            names = G.templates_for(tf)
            stats = self.state["template_stats"]
            w = np.array([1.0 + stats.get(f"{tf}|{n}", 0) for n in names])
            params.update(template=names[int(rng.choice(len(names), p=w / w.sum()))])
        elif kind in ("miner", "symreg"):
            params.update(horizon=int(rng.choice(G.HOLDS[tf][1:5])))
        elif kind == "random":
            params.update(samples=150)
        root = self.real_root if dataset == "real" else self.null_root
        return {"task_id": f"{self.cfg.run_id}-r{rnd}-{seq}", "kind": kind, "dataset": dataset, "tf": tf, "cell": cell, "seed": seed,
                "store_root": str(root), "vintage": self.vintages[vintage].as_dict(), "max_complexity": self.cfg.max_complexity,
                "risk_pct": self.cfg.risk_pct, "params": params}

    def validation_tasks(self, rnd: int, start_seq: int) -> list[dict]:
        tasks, seq = [], start_seq
        rows = self.db.execute("SELECT cid, dataset, tf, vintage, spec_z FROM candidates WHERE run_id=? AND stage='selected' AND oos IS NULL "
                               "AND created_round>0 ORDER BY dataset, tf, vintage, cid", (self.cfg.run_id,)).fetchall()
        groups: dict[tuple, list] = {}
        for r in rows:
            groups.setdefault((r["dataset"], r["tf"], r["vintage"]), []).append(r)
        for (dataset, tf, vintage), items in sorted(groups.items()):
            for i in range(0, len(items), 8):
                chunk = items[i:i + 8]
                root = self.real_root if dataset == "real" else self.null_root
                tasks.append({"task_id": f"{self.cfg.run_id}-r{rnd}-{seq}", "kind": "oos", "dataset": dataset, "tf": tf, "cell": f"{dataset}|{tf}|{vintage}|oos",
                              "seed": derive_seed(self.cfg.seed, rnd, seq), "store_root": str(root), "vintage": self.vintages[vintage].as_dict(),
                              "risk_pct": self.cfg.risk_pct,
                              "params": {"candidates": [{"hash": r["cid"], "spec": S.decompress(r["spec_z"])} for r in chunk]}})
                seq += 1
        for stage, kind, limit in (("statistically_validated", "robust", 6), ("robust", "confirm", 2)):
            col = "robust" if kind == "robust" else "confirm"
            rows = self.db.execute(f"SELECT cid, dataset, tf, vintage, spec_z FROM candidates WHERE run_id=? AND stage=? AND {col} IS NULL "
                                   "ORDER BY dataset, cid LIMIT ?", (self.cfg.run_id, stage, limit)).fetchall()
            for r in rows:
                if kind == "confirm" and r["dataset"] != "real":
                    continue
                root = self.real_root if r["dataset"] == "real" else self.null_root
                tasks.append({"task_id": f"{self.cfg.run_id}-r{rnd}-{seq}", "kind": kind, "dataset": r["dataset"], "tf": r["tf"],
                              "cell": f"{r['dataset']}|{r['tf']}|{r['vintage']}|{kind}", "seed": derive_seed(self.cfg.seed, rnd, seq),
                              "store_root": str(root), "vintage": self.vintages[r["vintage"]].as_dict(), "risk_pct": self.cfg.risk_pct,
                              "params": {"hash": r["cid"], "spec": S.decompress(r["spec_z"])}})
                seq += 1
        return tasks

    # ------------------------------------------------------------ applying results
    def apply_search(self, rnd: int, seq: int, task: dict, res: dict) -> None:
        run, ds, tf = self.cfg.run_id, task["dataset"], task["tf"]
        vintage = task["vintage"]["cutoff"]
        st = self.cell_stats(task["cell"])
        records = res["records"]
        exp_rows, cand_rows = [], []
        screened = 0
        for r in records:
            h = r.get("hash")
            cid = cid_of(ds, vintage, h) if h else None
            m = r.get("it") or r.get("iv") or {}
            exp_rows.append((run, rnd, task["task_id"], cid, ds, tf, vintage, r["stage"], int(bool(r.get("passed"))), r.get("reason"),
                             m.get("trades"), m.get("exp_r"), m.get("sharpe"), r.get("fitness"), r.get("complexity"), r.get("origin"), r.get("operator")))
            if r["stage"] == "screen" and r.get("spec") is not None:
                screened += bool(r.get("passed"))
                spec = r["spec"]
                cand_rows.append((run, cid, h, ds, tf, vintage, spec["direction"], r.get("origin"), r.get("operator"), json.dumps(r.get("parents", [])),
                                  S.compress(spec), r.get("complexity"), structural_family(spec), r.get("sig"), rnd, task["task_id"],
                                  "screened" if r.get("passed") else "experimental", None if r.get("passed") else "screen",
                                  None if r.get("passed") else r.get("reason"), r.get("fitness"), lab.dumps(r.get("it")), rnd))
        self.db.executemany("INSERT INTO experiments(run_id, round, task_id, cid, dataset, tf, vintage, stage, passed, reason, trades, exp_r, sharpe, "
                            "fitness, complexity, origin, operator) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", exp_rows)
        self.db.executemany("INSERT OR IGNORE INTO candidates(run_id, cid, spec_hash, dataset, tf, vintage, direction, origin, operator, parents, spec_z, "
                            "complexity, family, signature, created_round, task_id, stage, rejected_at, reject_reason, fitness, it, updated_round) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cand_rows)
        for r in records:  # inner-validation outcomes
            if r["stage"] != "inner_validation":
                continue
            cid = cid_of(ds, vintage, r["hash"])
            if r.get("selected"):
                fam = self.db.execute("SELECT family FROM candidates WHERE run_id=? AND cid=?", (run, cid)).fetchone()
                frozen = self.db.execute("SELECT count(*) FROM candidates WHERE run_id=? AND dataset=? AND tf=? AND vintage=? AND family=? AND stage NOT IN "
                                         "('experimental','screened')", (run, ds, tf, vintage, fam[0] if fam else None)).fetchone()[0]
                if frozen >= FAMILY_FREEZE_CAP:
                    # Same structure, different constants, already tested out of sample twice: another look would
                    # spend multiple-testing budget on a near-copy without new information.
                    self.db.execute("UPDATE candidates SET iv=?, rejected_at='freeze', reject_reason='near_duplicate_family', updated_round=? "
                                    "WHERE run_id=? AND cid=? AND stage='screened'", (lab.dumps(r.get("iv")), rnd, run, cid))
                    self.db.execute("INSERT INTO experiments(run_id, round, task_id, cid, dataset, tf, vintage, stage, passed, reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                    (run, rnd, task["task_id"], cid, ds, tf, vintage, "freeze", 0, "near_duplicate_family"))
                    continue
                self.db.execute("UPDATE candidates SET stage='selected', iv=?, rejected_at=NULL, reject_reason=NULL, updated_round=? "
                                "WHERE run_id=? AND cid=? AND stage IN ('screened','experimental')", (lab.dumps(r.get("iv")), rnd, run, cid))
            else:
                self.db.execute("UPDATE candidates SET iv=?, rejected_at=COALESCE(rejected_at, 'inner_validation'), "
                                "reject_reason=COALESCE(reject_reason, ?), updated_round=? WHERE run_id=? AND cid=? AND stage='screened'",
                                (lab.dumps(r.get("iv")), r.get("reason") if not r.get("passed") else "not_selected_for_diversity_or_rank", rnd, run, cid))
        scope = f"{ds}|{tf}|{vintage}"
        sigs = res.get("signatures", [])
        before = self.db.total_changes
        self.db.executemany("INSERT OR IGNORE INTO signatures VALUES(?,?,?)", [(run, scope, s) for s in sigs])
        new = self.db.total_changes - before
        novelty = new / max(1, len(sigs))
        n_eval = sum(1 for r in records if r["stage"] == "screen")
        st["tasks"] += 1
        st["evaluated"] += n_eval
        st["screen_passed"] += screened
        st["selected"] += len(res["selected"])
        st["novelty"] = (st["novelty"] + [novelty])[-4:]
        st["recent_selected"] = (st["recent_selected"] + [len(res["selected"])])[-4:]
        if len(st["novelty"]) >= 4 and max(st["novelty"]) < self.cfg.exhaustion_novelty and sum(st["recent_selected"]) == 0:
            st["retired"] = True
            self.log(f"cell {task['cell']} retired: novelty below {self.cfg.exhaustion_novelty:.0%} for 4 tasks with no selections")
        if res.get("archive"):
            self.db.execute("INSERT OR REPLACE INTO archives VALUES(?,?,?,?)", (run, task["cell"], rnd, json.dumps(res["archive"])))
        if res.get("findings"):
            self.db.execute("INSERT INTO findings(run_id, round, task_id, kind, dataset, tf, vintage, payload) VALUES(?,?,?,?,?,?,?,?)",
                            (run, rnd, task["task_id"], task["kind"], ds, tf, vintage, lab.dumps(res["findings"])))
        if task["kind"] == "template" and res["selected"]:
            key = f"{tf}|{task['params']['template']}"
            self.state["template_stats"][key] = self.state["template_stats"].get(key, 0) + len(res["selected"])
        if res["selected"]:  # training-window success raises the weight of the features involved
            w = self.state["feature_weights"].setdefault(f"{ds}|{tf}", {})
            for h, spec in res["selected_specs"].items():
                for f in S.leaves(spec):
                    w[f] = min(5.0, w.get(f, 1.0) + 0.25)
        self._task_row(rnd, seq, task, res, n_eval, len(res["selected"]), novelty)

    def _task_row(self, rnd, seq, task, res, evaluated=None, selected=None, novelty=None):
        p = {k: v for k, v in task["params"].items() if k not in ("candidates", "seeds", "spec")}
        self.db.execute("INSERT OR REPLACE INTO tasks(task_id, run_id, round, seq, kind, dataset, tf, vintage, cell, seed, params, status, runtime_s, cpu_s, "
                        "peak_rss_mb, pid, evaluated, selected, novelty, error, superseded) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                        (task["task_id"], self.cfg.run_id, rnd, seq, task["kind"], task["dataset"], task["tf"], task.get("vintage", {}).get("cutoff"),
                         task.get("cell"), task["seed"], json.dumps(p, default=str)[:4000], "done" if res.get("ok") else "failed", res.get("runtime_s"),
                         res.get("cpu_s"), res.get("peak_rss_mb"), res.get("pid"), evaluated, selected, novelty, res.get("error")))

    def apply_oos(self, rnd, seq, task, res) -> None:
        from quantresearch.discovery import validate as V
        from quantresearch.discovery.benchmarks import max_abs_correlation
        run = self.cfg.run_id
        for item in res["results"]:
            m = item["metrics"]
            days, rets = V.unpack(item["days"]), V.unpack(item["rets"])
            corr, corr_detail = max_abs_correlation(days, rets, self.benchmarks) if task["dataset"] == "real" else (None, {})
            m["correlation_with_existing"] = corr_detail
            stage = "walk_forward" if item["passed"] else "selected"
            self.db.execute("UPDATE candidates SET oos=?, stage=?, rejected_at=?, reject_reason=?, max_corr_existing=?, updated_round=? WHERE run_id=? AND cid=?",
                            (lab.dumps({**m, "regimes": item["regimes"]}), stage, None if item["passed"] else "walk_forward",
                             None if item["passed"] else item["reason"], corr, rnd, run, item["hash"]))
            self.db.execute("INSERT OR REPLACE INTO oos_series(run_id, cid, days, rets, sharpe, t_nw, p_nw) VALUES(?,?,?,?,?,?,?)",
                            (run, item["hash"], item["days"], item["rets"], m["sharpe"], m.get("t_nw"), m.get("p_nw")))
            for year, f in (m.get("years_detail") or {}).items():
                self.db.execute("INSERT OR REPLACE INTO folds VALUES(?,?,?,?,?,?)", (run, item["hash"], year, f["trades"], f["total_r"], f["exp_r"]))
            self.db.execute("INSERT INTO experiments(run_id, round, task_id, cid, dataset, tf, vintage, stage, passed, reason, trades, exp_r, sharpe) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (run, rnd, task["task_id"], item["hash"], task["dataset"], task["tf"],
                                                                   task["vintage"]["cutoff"], "walk_forward_oos", int(item["passed"]), item["reason"],
                                                                   m["trades"], m.get("exp_r"), m["sharpe"]))
            if item["passed"] and task["dataset"] == "real":
                self.state["last_wf_round"] = rnd
        self._task_row(rnd, seq, task, res, len(res["results"]))

    def apply_robust(self, rnd, seq, task, res) -> None:
        run = self.cfg.run_id
        passed = res["stage5_passed"]
        self.db.execute("UPDATE candidates SET robust=?, stage=?, rejected_at=?, reject_reason=?, updated_round=? WHERE run_id=? AND cid=?",
                        (lab.dumps(res), "robust" if passed else "statistically_validated", None if passed else "robustness_adversarial",
                         None if passed else ",".join(res["failed"])[:500], rnd, run, res["hash"]))
        self.db.execute("INSERT INTO experiments(run_id, round, task_id, cid, dataset, tf, vintage, stage, passed, reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (run, rnd, task["task_id"], res["hash"], task["dataset"], task["tf"], task["vintage"]["cutoff"], "robustness_adversarial",
                         int(passed), ",".join(res["failed"]) or "passed"))
        self._task_row(rnd, seq, task, res, 1)

    def apply_confirm(self, rnd, seq, task, res) -> None:
        run = self.cfg.run_id
        ok = res["confirm"]["passed"]
        self.db.execute("UPDATE candidates SET confirm=?, stage=?, rejected_at=?, reject_reason=?, updated_round=? WHERE run_id=? AND cid=?",
                        (lab.dumps(res["confirm"]), "qualified" if ok else "robust", None if ok else "engine_confirmation",
                         None if ok else "failed engine or M1 confirmation", rnd, run, res["hash"]))
        self.db.execute("INSERT INTO experiments(run_id, round, task_id, cid, dataset, tf, vintage, stage, passed, reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (run, rnd, task["task_id"], res["hash"], task["dataset"], task["tf"], task["vintage"]["cutoff"], "engine_confirmation", int(ok),
                         "passed" if ok else "failed"))
        if ok:
            self.log(f"candidate {res['hash']} qualified for paper trading only (research qualification; live trading stays off)")
        self._task_row(rnd, seq, task, res, 1)

    # ------------------------------------------------------------ stage 3 and ranking
    def refresh_statistics(self, rnd: int) -> dict:
        from quantresearch.discovery import validate as V
        run = self.cfg.run_id
        summary = {}
        rows = self.db.execute("SELECT c.cid, c.dataset, c.tf, c.vintage, c.stage, c.stage3, s.sharpe, s.t_nw, s.p_nw FROM candidates c "
                               "JOIN oos_series s ON s.run_id=c.run_id AND s.cid=c.cid WHERE c.run_id=? ORDER BY c.cid", (run,)).fetchall()
        by_ds = {"real": [], "null": []}
        sample_left: dict[tuple, int] = {}
        need = []
        for r in rows:
            passed2 = r["stage"] not in ("selected",)
            key = (r["dataset"], r["tf"], r["vintage"])
            take = passed2 or sample_left.get(key, 60) > 0 or r["p_nw"] is None
            if take and not passed2:
                sample_left[key] = sample_left.get(key, 60) - 1
            item = {"hash": r["cid"], "tf": r["tf"], "vintage": r["vintage"], "rets": None, "sharpe": r["sharpe"], "t_nw": r["t_nw"], "p_nw": r["p_nw"],
                    "stage2_passed": passed2, "row": r, "ci": (json.loads(r["stage3"]) or {}).get("sharpe_ci95") if r["stage3"] else None}
            by_ds[r["dataset"]].append(item)
            if take:
                need.append(item)
        for item in need:  # series are read only for survivors and a bounded, deterministic sample
            blob = self.db.execute("SELECT rets FROM oos_series WHERE run_id=? AND cid=?", (run, item["hash"])).fetchone()[0]
            item["rets"] = V.unpack(blob)
        null_pass = {}
        null_all = {}
        for c in by_ds["null"]:
            null_all.setdefault(c["tf"], []).append(c["sharpe"])
            if c["stage2_passed"]:
                null_pass.setdefault(c["tf"], []).append(c["sharpe"])
        enough = sum(len(v) for v in null_pass.values()) >= V.GATES["null_min_samples"]
        null_ref = null_pass if enough else null_all
        summary["null_reference"] = "null candidates that passed walk-forward" if enough else "all null candidates evaluated out of sample"
        for ds in ("real", "null"):
            res = V.stage3(by_ds[ds], null_ref)
            for c in by_ds[ds]:
                r = c["row"]
                if not c["stage2_passed"]:
                    continue
                s3 = res.get(c["hash"])
                if s3 is None:
                    continue
                if s3["passed"]:
                    new_stage = r["stage"] if r["stage"] in ("statistically_validated", "robust", "qualified", "paper_trading") else "statistically_validated"
                    self.db.execute("UPDATE candidates SET stage3=?, stage=?, rejected_at=CASE WHEN rejected_at='statistics' THEN NULL ELSE rejected_at END, "
                                    "reject_reason=CASE WHEN rejected_at='statistics' THEN NULL ELSE reject_reason END WHERE run_id=? AND cid=?",
                                    (lab.dumps(s3), new_stage, run, c["hash"]))
                else:
                    # As more candidates are looked at, the bar rises; a candidate can lose a status it held earlier.
                    self.db.execute("UPDATE candidates SET stage3=?, stage='walk_forward', rejected_at='statistics', reject_reason=? WHERE run_id=? AND cid=?",
                                    (lab.dumps(s3), ",".join(s3["reasons"]), run, c["hash"]))
            summary[ds] = {"oos_looks": len(by_ds[ds]), "stage3_passed": sum(1 for v in res.values() if v["passed"])}
        return summary

    def refresh_ranking(self, rnd: int | None = None) -> None:
        """Scores depend only on each candidate's own evidence, so only changed candidates need rescoring each round.
        Statistics drift slowly for everyone as looks accumulate, so all are rescored every 25 rounds and at the end."""
        run = self.cfg.run_id
        if rnd is None or rnd % 25 == 0:
            rows = self.db.execute("SELECT cid, stage, direction, complexity, oos, stage3, robust, max_corr_existing FROM candidates WHERE run_id=? "
                                   "AND oos IS NOT NULL", (run,)).fetchall()
        else:
            rows = self.db.execute("SELECT cid, stage, direction, complexity, oos, stage3, robust, max_corr_existing FROM candidates WHERE run_id=? "
                                   "AND updated_round=? AND oos IS NOT NULL", (run, rnd)).fetchall()
        for r in rows:
            c = {"cid": r["cid"], "stage": r["stage"], "direction": r["direction"], "complexity": r["complexity"], "oos": json.loads(r["oos"]),
                 "stage3": json.loads(r["stage3"]) if r["stage3"] else None, "robust": json.loads(r["robust"]) if r["robust"] else None,
                 "max_corr_existing": r["max_corr_existing"]}
            self.db.execute("UPDATE candidates SET ranking=? WHERE run_id=? AND cid=?", (lab.dumps(ranking.score(c)), run, r["cid"]))

    # ------------------------------------------------------------ progress
    def counts(self) -> dict:
        run = self.cfg.run_id
        q = lambda sql, *a: self.db.execute(sql, (run, *a)).fetchone()[0]  # noqa: E731
        stages = {r[0]: r[1] for r in self.db.execute("SELECT stage, count(*) FROM candidates WHERE run_id=? AND dataset='real' AND created_round>=0 GROUP BY stage", (run,))}
        null_stages = {r[0]: r[1] for r in self.db.execute("SELECT stage, count(*) FROM candidates WHERE run_id=? AND dataset='null' AND created_round>=0 GROUP BY stage", (run,))}
        return {"experiments": q("SELECT count(*) FROM experiments WHERE run_id=? AND superseded=0"),
                "hypotheses": q("SELECT count(*) FROM candidates WHERE run_id=? AND created_round>=0"),
                "families": q("SELECT count(DISTINCT family) FROM candidates WHERE run_id=? AND dataset='real' AND created_round>=0"),
                "families_walk_forward": q("SELECT count(DISTINCT family) FROM candidates WHERE run_id=? AND dataset='real' AND oos IS NOT NULL AND stage!='selected'"),
                "stages_real": stages, "stages_null": null_stages,
                "tasks": q("SELECT count(*) FROM tasks WHERE run_id=? AND superseded=0"),
                "failed_tasks": q("SELECT count(*) FROM tasks WHERE run_id=? AND superseded=0 AND status='failed'"),
                "live_cells": len(self.live_cells("real")), "total_cells_real": len([c for c in self.cells() if c.startswith("real|")])}

    def elapsed(self) -> float:
        """Active research time: wall-clock time minus periods when the computer was asleep."""
        if not hasattr(self, "_base_elapsed"):
            self._base_elapsed = float(self.state.get("elapsed_s", 0.0))
            self._base_suspended = float(self.state.get("suspended_s", 0.0))
        asleep = self.monitor.suspended_s if hasattr(self, "monitor") else 0.0
        return self._base_elapsed + (time.time() - self.session_start) - asleep

    def suspended(self) -> float:
        if not hasattr(self, "_base_suspended"):
            self.elapsed()
        return self._base_suspended + (self.monitor.suspended_s if hasattr(self, "monitor") else 0.0)

    def write_progress(self, status: str, extra: dict | None = None, full: bool = True) -> None:
        if full or not hasattr(self, "_counts"):
            self._counts = self.counts()
        body = {"run_id": self.cfg.run_id, "status": status, "updated_at": now(), "round": self.state["round"], "elapsed_s": self.elapsed(),
                "suspended_s": self.suspended(), "wall_clock_s": self.elapsed() + self.suspended(),
                "max_hours": self.cfg.max_hours, "workers": self.workers, "counts": self._counts, "holdout_note": HOLDOUT_NOTE, **(extra or {})}
        tmp = self.dir / "progress.json.tmp"
        tmp.write_text(json.dumps(lab.jsonable(body), indent=1), encoding="utf-8")
        os.replace(tmp, self.dir / "progress.json")

    # ------------------------------------------------------------ stop logic
    def stop_reason(self) -> str | None:
        if self.stop_requested or (self.dir / "STOP").exists():
            return "stop requested"
        if self.elapsed() >= self.cfg.max_hours * 3600:
            return f"time limit of {self.cfg.max_hours} hours reached"
        if self.state["experiments"] >= self.cfg.max_experiments:
            return f"experiment limit of {self.cfg.max_experiments} reached"
        if self.state["round"] >= self.cfg.max_rounds:
            return f"round limit of {self.cfg.max_rounds} reached"
        if not self.live_cells("real"):
            return "search space exhausted: every real-data cell retired for lack of novelty"
        if self.elapsed() >= self.cfg.futility_min_hours * 3600:
            quiet = self.state["round"] - self.state.get("last_wf_round", 0) >= self.cfg.futility_window_rounds
            validated = self.db.execute("SELECT count(*) FROM candidates WHERE run_id=? AND dataset='real' AND stage IN "
                                        "('statistically_validated','robust','qualified','paper_trading')", (self.cfg.run_id,)).fetchone()[0]
            if quiet and validated == 0:
                return (f"futility: after {self.cfg.futility_min_hours} hours, no candidate is statistically validated and none passed "
                        f"walk-forward in the last {self.cfg.futility_window_rounds} rounds")
        return None

    def _signals(self) -> None:
        def handler(signum, frame):
            if self.stop_requested:
                raise KeyboardInterrupt
            self.stop_requested = True
            self.log("stop signal received: finishing the current round, then stopping")
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            if hasattr(signal, name):
                try:
                    signal.signal(getattr(signal, name), handler)
                except (ValueError, OSError):
                    pass

    # ------------------------------------------------------------ main loop
    def run_tasks(self, tasks: list[dict], timeout: float) -> dict[str, dict]:
        futures = {self.pool.submit(execute, t): t["task_id"] for t in tasks}
        done, pending = wait(futures, timeout=timeout)
        out = {}
        for f in done:
            try:
                out[futures[f]] = f.result()
            except Exception as exc:  # a worker died (for example out of memory)
                out[futures[f]] = {"task_id": futures[f], "ok": False, "error": f"worker failure: {type(exc).__name__}: {exc}"}
        if pending:
            for f in pending:
                out[futures[f]] = {"task_id": futures[f], "ok": False, "error": f"timed out after {timeout:.0f}s"}
            self.log(f"{len(pending)} task(s) timed out; restarting workers", "warn")
            self.restart_pool(kill=True)
        if any(not r.get("ok") and "worker failure" in str(r.get("error")) for r in out.values()):
            self.restart_pool(kill=True)
        return out

    def memory_guard(self) -> None:
        import psutil
        waited = 0
        while psutil.virtual_memory().available / 1e9 < self.cfg.min_free_memory_gb and waited < 600:
            if psutil.virtual_memory().available / 1e9 < self.cfg.critical_free_memory_gb and self.workers > 1:
                self.log(f"free memory critical ({psutil.virtual_memory().available / 1e9:.2f} GB): reducing workers to {self.workers - 1}", "warn")
                self.restart_pool(self.workers - 1)
            if waited == 0:
                self.log(f"free memory below {self.cfg.min_free_memory_gb} GB: pausing new work", "warn")
            time.sleep(15)
            waited += 15

    def run(self) -> str:
        self._signals()
        keep_awake(True)
        lock = self.dir / "controller.lock"
        if lock.exists():
            try:
                import psutil
                pid = int(lock.read_text().strip() or 0)
                if pid and psutil.pid_exists(pid) and pid != os.getpid():
                    raise SystemExit(f"Another controller (pid {pid}) is running this run.")
            except ValueError:
                pass
        lock.write_text(str(os.getpid()))
        reason = "unknown"
        try:
            if getattr(self, "_retro_suspended", 0) > 0:
                self.log(f"{self._retro_suspended / 3600:.2f} h of earlier rounds were spent with the computer asleep; removed from research time", "warn")
            if hasattr(self, "_resumed_at"):
                self.log(f"resumed from verified checkpoint at round {self._resumed_at} with code {code_version()}; any interrupted round is recomputed with the same seeds")
            self.log(f"run {self.cfg.run_id}: seed {self.cfg.seed}, up to {self.cfg.max_hours} h, {self.workers} workers. {HOLDOUT_NOTE}")
            self.db.execute("UPDATE runs SET status='running' WHERE run_id=?", (self.cfg.run_id,))
            self.db.commit()
            self.ensure_stores()
            self.start_pool()
            self.monitor.start()
            bench_path = self.dir / "benchmarks.parquet"
            if self.cfg.benchmarks and not bench_path.exists():
                self.log("computing the seven existing engines' daily returns, for correlation")
                res = self.run_tasks([{"task_id": f"{self.cfg.run_id}-benchmarks", "kind": "benchmarks", "dataset": "real", "tf": "h1", "seed": 0,
                                       "out": str(bench_path), "params": {}}], timeout=3600)
                r = next(iter(res.values()))
                self.log(f"benchmarks: {r.get('engines')} errors {r.get('errors') or r.get('error')}")
            from quantresearch.discovery.benchmarks import load
            self.benchmarks = load(bench_path)
            tasks_per_round = self.cfg.tasks_per_round or 8
            while True:
                reason = self.stop_reason()
                if reason:
                    break
                self.memory_guard()
                if self.state["round"] - getattr(self, "pool_started_round", 0) >= self.cfg.worker_task_limit:
                    self.restart_pool()  # fresh worker processes release any memory they accumulated
                rnd = self.state["round"] + 1
                started = now()
                rng = np.random.default_rng(derive_seed(self.cfg.seed, rnd, "schedule"))
                tasks = self.validation_tasks(rnd, 0)
                n_search = max(2, tasks_per_round - len(tasks)) if len(tasks) < tasks_per_round else 2
                for i, cell in enumerate(self.pick_cells(rng, n_search)):
                    tasks.append(self.search_task(cell, rnd, len(tasks)))
                results = self.run_tasks(tasks, timeout=3600)
                self.in_round = True
                if os.environ.get("QR_DISCOVERY_CRASH_IN_ROUND") == str(rnd):
                    self.db.execute("INSERT INTO experiments(run_id, round, stage) VALUES(?,?,?)", (self.cfg.run_id, rnd, "crash_probe"))
                    os._exit(4)  # test hook: a hard crash part-way through applying a round
                for seq, task in enumerate(tasks):
                    res = results.get(task["task_id"], {"ok": False, "error": "missing"})
                    if not res.get("ok"):
                        self.log(f"task {task['task_id']} ({task['kind']} {task.get('cell')}) failed: {res.get('error')}", "error")
                        self._task_row(rnd, seq, task, res)
                        if task["kind"] in KINDS:
                            self.cell_stats(task["cell"])["tasks"] += 1
                        continue
                    {"oos": self.apply_oos, "robust": self.apply_robust, "confirm": self.apply_confirm}.get(task["kind"], self.apply_search)(rnd, seq, task, res)
                stats3 = self.refresh_statistics(rnd)
                self.refresh_ranking(rnd)
                usage = self.monitor.drain()
                self.db.execute("INSERT INTO resources VALUES(?,?,?,?,?,?,?,?,?)", (self.cfg.run_id, now(), rnd, usage.get("cpu_pct"), usage.get("rss_mb"),
                                                                                    usage.get("peak_rss_mb"), usage.get("sys_avail_mb"), self.workers, None))
                n_exp = self.db.execute("SELECT count(*) FROM experiments WHERE run_id=? AND round=? AND superseded=0", (self.cfg.run_id, rnd)).fetchone()[0]
                self.db.execute("INSERT OR REPLACE INTO rounds VALUES(?,?,?,?,?,?,?)", (self.cfg.run_id, rnd, started, now(), len(tasks), n_exp, "done"))
                self.state["round"] = rnd
                self.state["experiments"] += n_exp
                self.checkpoint()  # commits the round
                self.in_round = False
                if os.environ.get("QR_DISCOVERY_CRASH_AFTER_ROUND") == str(rnd):
                    os._exit(3)  # test hook: simulates a hard crash right after a committed round

                self.write_progress("running", {"stage3": stats3, "resources": usage, "current_stage": "search and validation rounds"}, full=rnd % 10 == 0)
                if rnd % 10 == 0:
                    c = self._counts
                    self.log(f"round {rnd}: {c['experiments']} experiments, {c['hypotheses']} hypotheses, stages {c['stages_real']}, "
                             f"null {c['stages_null']}, {usage.get('cpu_pct', 0):.0f}% CPU, {usage.get('rss_mb', 0):.0f} MB")
                if rnd % 50 == 0:
                    self.interim_report()
            self.refresh_ranking()
            self.db.commit()
            self.log(f"stopping: {reason}")
        except BaseException:
            # Mid-round failure: discard the round's uncommitted writes. Resume recomputes it.
            self.db.rollback()
            self.in_round = False
            self.event_buffer = []
            raise
        finally:
            if self.pool is not None:
                self.pool.shutdown(wait=True, cancel_futures=True)
            self.monitor.alive = False
            keep_awake(False)
            lock.unlink(missing_ok=True)
        final = reason not in ("stop requested",)
        status = "completed" if final else "stopped"
        self.db.execute("UPDATE runs SET status=?, ended_at=?, stop_reason=? WHERE run_id=?", (status, now(), reason, self.cfg.run_id))
        self.db.commit()
        self.write_progress(status, {"stop_reason": reason, "current_stage": "finished" if final else "stopped; resume to continue"})
        if final:
            from quantresearch.discovery import report
            path = report.write(self.cfg.run_id, self.p, final=True)
            self.log(f"final report: {path}")
        return reason

    def interim_report(self) -> None:
        try:
            from quantresearch.discovery import report
            report.write(self.cfg.run_id, self.p, final=False)
        except Exception as exc:
            self.log(f"interim report failed: {type(exc).__name__}: {exc}", "warn")


# ---------------------------------------------------------------- CLI
def config_from(args) -> DiscoveryConfig:
    cfg = DiscoveryConfig(run_id=args.run_id)
    path = run_dir(args.run_id) / "config.json"
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        for k, v in saved.items():
            if hasattr(cfg, k):
                setattr(cfg, k, tuple(v) if isinstance(getattr(cfg, k), tuple) else v)
    for k in ("hours", "seed", "workers", "max_experiments", "timeframes", "vintages", "tasks_per_round", "null_share", "store_dir",
              "gp_population", "gp_generations", "futility_min_hours", "max_rounds"):
        v = getattr(args, k, None)
        if v is not None:
            setattr(cfg, "max_hours" if k == "hours" else k, tuple(v) if k in ("timeframes", "vintages") else v)
    return cfg


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Autonomous strategy discovery (research only).")
    ap.add_argument("command", choices=["run", "supervise", "status", "stop", "report"])
    ap.add_argument("--run-id", default="main")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--hours", type=float)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--workers", type=int)
    ap.add_argument("--max-experiments", type=int)
    ap.add_argument("--max-rounds", type=int)
    ap.add_argument("--timeframes", nargs="*")
    ap.add_argument("--vintages", nargs="*")
    ap.add_argument("--tasks-per-round", type=int)
    ap.add_argument("--null-share", type=float)
    ap.add_argument("--store-dir")
    ap.add_argument("--gp-population", type=int)
    ap.add_argument("--gp-generations", type=int)
    ap.add_argument("--futility-min-hours", type=float)
    ap.add_argument("--max-restarts", type=int, default=20)
    args = ap.parse_args(argv)
    d = run_dir(args.run_id)
    if args.command == "status":
        f = d / "progress.json"
        print(f.read_text(encoding="utf-8") if f.exists() else f"No progress for {args.run_id}")
        return
    if args.command == "stop":
        d.mkdir(parents=True, exist_ok=True)
        (d / "STOP").write_text(now())
        print("Stop requested. The controller finishes its round, checkpoints and exits.")
        return
    if args.command == "report":
        from quantresearch.discovery import report
        print(report.write(args.run_id, final=False))
        return
    cfg = config_from(args)
    d.mkdir(parents=True, exist_ok=True)
    if not (d / "config.json").exists() or not args.resume:
        (d / "config.json").write_text(json.dumps({k: (list(v) if isinstance(v, tuple) else v) for k, v in cfg.__dict__.items()}, indent=1), encoding="utf-8")
    if args.command == "run":
        (d / "STOP").unlink(missing_ok=True)
        reason = Controller(cfg, resume=args.resume).run()
        print(json.dumps({"run_id": cfg.run_id, "stop_reason": reason}))
        return
    # supervise: run the controller in a child process and resume it after any crash.
    (d / "STOP").unlink(missing_ok=True)
    restarts = 0
    resume = args.resume
    while True:
        cmd = [sys.executable, "-m", "quantresearch.discovery.controller", "run", "--run-id", cfg.run_id]
        if resume:
            cmd.append("--resume")
        for k, flag in (("max_hours", "--hours"), ("workers", "--workers")):
            v = getattr(cfg, k)
            if v is not None:
                cmd += [flag, str(v)]
        with open(d / "supervisor.log", "a", encoding="utf-8") as log:
            log.write(f"{now()} start {' '.join(cmd)}\n")
            log.flush()
            code = subprocess.call(cmd, stdout=log, stderr=subprocess.STDOUT)
            log.write(f"{now()} exit {code}\n")
        if code == 0 or (d / "STOP").exists():
            break
        restarts += 1
        if restarts > args.max_restarts:
            with open(d / "supervisor.log", "a", encoding="utf-8") as log:
                log.write(f"{now()} giving up after {restarts - 1} restarts\n")
            break
        resume = True
        time.sleep(30)


if __name__ == "__main__":
    main()
