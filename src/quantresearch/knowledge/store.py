"""Full-text research store on SQLite FTS5.

Every document says what KIND of evidence it is, so a backtest finding cannot pass for a fact and a model
prediction cannot pass for a finding. Retrieval returns sources with those labels. The layer retrieves and
attributes; it writes no conclusions. Anything that summarizes results must cite what this returns.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from quantresearch.core.config import ROOT, Paths, paths

KINDS = {"historical_fact", "live_observation", "statistical_finding", "model_prediction", "research_hypothesis", "unverified_assumption", "documentation"}


@dataclass
class Evidence:
    id: int
    kind: str
    title: str
    source: str
    published_at: str | None
    snippet: str
    score: float


class KnowledgeBase:
    def __init__(self, p: Paths | None = None, path: str | Path | None = None):
        if path is None:
            p = p or paths()
            p.app.mkdir(parents=True, exist_ok=True)
            path = p.app / "knowledge.sqlite"
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.execute("CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY, uid TEXT UNIQUE, kind TEXT, title TEXT, source TEXT, published_at TEXT, ingested_at TEXT, body TEXT)")
        self.con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(title, body, content='docs', content_rowid='id', tokenize='porter')")
        self.con.commit()

    def add(self, uid: str, kind: str, title: str, body: str, source: str, published_at: str | None = None) -> int:
        """Idempotent on uid: re-ingesting replaces the document."""
        if kind not in KINDS:
            raise ValueError(f"Unknown evidence kind {kind!r}")
        old = self.con.execute("SELECT id, title, body FROM docs WHERE uid=?", (uid,)).fetchone()
        if old:
            self.con.execute("INSERT INTO fts(fts, rowid, title, body) VALUES('delete', ?, ?, ?)", old)
            self.con.execute("DELETE FROM docs WHERE id=?", (old[0],))
        cur = self.con.execute("INSERT INTO docs(uid,kind,title,source,published_at,ingested_at,body) VALUES(?,?,?,?,?,?,?)",
                               (uid, kind, title, source, published_at, datetime.now(timezone.utc).isoformat(), body))
        self.con.execute("INSERT INTO fts(rowid, title, body) VALUES(?,?,?)", (cur.lastrowid, title, body))
        self.con.commit()
        return int(cur.lastrowid)

    def search(self, query: str, kinds: set[str] | None = None, as_of: str | None = None, limit: int = 10) -> list[Evidence]:
        """Ranked evidence. `as_of` hides anything published later, for use inside historical replays."""
        terms = re.findall(r"\w+", query)
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        where, params = ["fts MATCH ?"], [match]
        if kinds:
            where.append(f"docs.kind IN ({','.join('?' * len(kinds))})")
            params += sorted(kinds)
        if as_of:
            # An undated document cannot be shown to be from before as_of, so it is left out.
            where.append("docs.published_at IS NOT NULL AND docs.published_at <= ?")
            params.append(as_of)
        sql = (f"SELECT docs.id, docs.kind, docs.title, docs.source, docs.published_at, snippet(fts, 1, '[', ']', ' ... ', 24), bm25(fts) "
               f"FROM fts JOIN docs ON docs.id = fts.rowid WHERE {' AND '.join(where)} ORDER BY bm25(fts) LIMIT ?")
        rows = self.con.execute(sql, params + [limit]).fetchall()
        return [Evidence(r[0], r[1], r[2], r[3], r[4], r[5], float(r[6])) for r in rows]

    def assist(self, question: str, as_of: str | None = None, limit: int = 8) -> dict:
        """Retrieval-based help: evidence grouped by kind, with no conclusion invented on top."""
        evidence = self.search(question, as_of=as_of, limit=limit)
        grouped: dict[str, list] = {}
        for e in evidence:
            grouped.setdefault(e.kind, []).append({"title": e.title, "source": e.source, "published_at": e.published_at, "snippet": e.snippet})
        return {"question": question, "as_of": as_of, "evidence": grouped, "found": len(evidence),
                "note": "Retrieved sources only. Check each one. Findings and predictions are not facts." if evidence else "Nothing relevant was found."}

    def count(self) -> dict:
        return dict(self.con.execute("SELECT kind, COUNT(*) FROM docs GROUP BY kind").fetchall())

    # ------------------------------------------------------------ ingestion
    def ingest_documentation(self) -> int:
        count = 0
        readme = ROOT / "README.md"
        if readme.exists():
            for title, body in _sections(readme.read_text(encoding="utf-8")):
                self.add(f"readme/{title}", "documentation", title, body, "README.md")
                count += 1
        from quantresearch.skills.registry import SKILLS
        for key, skill in SKILLS.items():
            self.add(f"skill/{key}", "documentation", skill.name, skill.instructions(), f"Original skill source: {skill.upstream_version}")
            count += 1
        from quantresearch.strategies import REGISTRY
        for name, defn in REGISTRY.items():
            body = f"{defn.methodology}\nLimitations: {' '.join(defn.limitations)}"
            self.add(f"strategy/{name}", "documentation", defn.title, body, "strategies/engines.py")
            hypotheses = defn.hypotheses.__name__
            self.add(f"strategy_hypotheses/{name}", "research_hypothesis", f"{defn.title} hypotheses", _hypothesis_text(defn), f"strategies/engines.py:{hypotheses}")
            count += 2
        return count

    def ingest_folder(self, folder: Path | None = None) -> int:
        """Your own literature and notes: text files in data/research/library/<kind>/.

        The folder name says what kind of evidence a file is, so a paper's claim stays labelled and nothing is
        promoted to fact by being stored here. Copyright and licensing of what you add stay your responsibility.
        """
        folder = folder or paths().research / "library"
        count = 0
        for kind in KINDS:
            base = folder / kind
            for path in sorted(base.rglob("*")) if base.exists() else []:
                if path.suffix.lower() in (".txt", ".md") and path.is_file():
                    stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
                    self.add(f"library/{kind}/{path.name}", kind, path.stem, path.read_text(encoding="utf-8", errors="replace"), path.name, stamp)
                    count += 1
        return count

    def ingest_reports(self, folder: Path | None = None) -> int:
        folder = folder or paths().research / "reports"
        count = 0
        for path in sorted(folder.glob("*_latest.json")) if folder.exists() else []:
            report = json.loads(path.read_text(encoding="utf-8"))
            verdict = report.get("verdict", {})
            lines = [f"Strategy {report.get('title')}: verdict {verdict.get('label')}. Out-of-sample Sharpe {verdict.get('oos_sharpe')}, "
                     f"deflated Sharpe {verdict.get('deflated_sharpe')}, trades {verdict.get('oos_trades')}. Holdout net {verdict.get('holdout_net')}."]
            for h in report.get("hypotheses", []):
                if h.get("tested"):
                    lines.append(f"{h['id']}: {h['statement']}. Events {h['events']}, excess {h['excess_mean']:.6f}, p {h['p_one_sided']:.4f}, "
                                 f"adjusted p {h.get('p_bh', float('nan')):.4f}.")
            self.add(f"report/{report['strategy']}", "statistical_finding", f"{report.get('title')} research report", "\n".join(lines),
                     str(path.name), report.get("created_at"))
            count += 1
        return count

    def ingest_model_cards(self, rows: list[dict]) -> int:
        for r in rows:
            best = r["metrics"].get("best")
            self.add(f"model/{r['id']}", "model_prediction", f"Model {r['id']} {r['name']} ({r['task']})",
                     f"Task {r['task']}. Beats constant baseline: {bool(r['beats_baseline'])}. Best candidate {best}. Trained through {r['train_end']}. Metrics {json.dumps(r['metrics'].get('walk_forward', {}), default=str)[:1500]}",
                     "models/registry.sqlite", r["created_at"])
        return len(rows)

    def ingest_news(self, items: list[dict]) -> int:
        for item in items:
            stamp = item.get("published_ts")
            self.add(f"news/{item.get('url') or item.get('title')}", "live_observation", item.get("title", ""), item.get("title", ""),
                     item.get("domain") or item.get("source") or "news", None if stamp is None else str(stamp))
        return len(items)


def _sections(text: str) -> list[tuple[str, str]]:
    parts = re.split(r"(?m)^##\s+(.+)$", text)
    return [(parts[i].strip(), parts[i + 1].strip()) for i in range(1, len(parts) - 1, 2)]


def _hypothesis_text(defn) -> str:
    import inspect
    return inspect.getsource(defn.hypotheses)
