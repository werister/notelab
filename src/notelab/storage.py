"""
Local storage — the experiment ledger and the catalog index.

Plain SQLite, one file, no ORM, no server. This is deliberately boring and inspectable:
`sqlite3 ~/.local/state/nlm-research-mcp/store.db .dump` shows you everything the server
has ever learned. The ledger is what makes the research self-improving across runs; the
catalog is the M2 Obsidian-alternative index (schema created now, populated later).
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path.home() / ".local/state/nlm-research-mcp/store.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    question      TEXT NOT NULL,
    winner        TEXT,
    winner_score  REAL,
    weights_json  TEXT NOT NULL,
    result_json   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS arms (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id  INTEGER NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
    strategy_name  TEXT NOT NULL,
    prompt         TEXT NOT NULL,
    quality_score  REAL,
    n_sources      INTEGER,
    notebook_id    TEXT,
    task_id        TEXT,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_arms_experiment ON arms(experiment_id);
CREATE INDEX IF NOT EXISTS idx_exp_question ON experiments(question);

-- M2 catalog (the indexed filesystem for agents). Created now, filled by catalog.sync.
CREATE TABLE IF NOT EXISTS notebooks (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    synced_at   TEXT
);
CREATE TABLE IF NOT EXISTS sources (
    id          TEXT PRIMARY KEY,
    notebook_id TEXT REFERENCES notebooks(id) ON DELETE CASCADE,
    title       TEXT,
    url         TEXT,
    domain      TEXT,
    synced_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_sources_notebook ON sources(notebook_id);
CREATE VIRTUAL TABLE IF NOT EXISTS sources_fts
    USING fts5(id, title, url, tokenize='porter unicode61 remove_diacritics 2');

-- M3 strategy memory: one tuned profile per research topic (question).
CREATE TABLE IF NOT EXISTS topic_profiles (
    topic        TEXT PRIMARY KEY,
    best_prompt  TEXT,
    best_score   REAL,
    weights_json TEXT,
    runs         INTEGER,
    updated_at   TEXT
);

-- M4 multi-model sparring: per-family attributed results over one shared question.
CREATE TABLE IF NOT EXISTS sparring (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    question    TEXT NOT NULL,
    prompt      TEXT NOT NULL,
    winner      TEXT,
    result_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sparring_families (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sparring_id   INTEGER NOT NULL REFERENCES sparring(id) ON DELETE CASCADE,
    family        TEXT NOT NULL,
    quality_score REAL,
    n_sources     INTEGER,
    available     INTEGER,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_spar_family ON sparring_families(family);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(_SCHEMA)
        self._migrate_fts_tokenizer()
        self.conn.commit()

    def _migrate_fts_tokenizer(self) -> None:
        """`CREATE VIRTUAL TABLE IF NOT EXISTS` won't retrofit a tokenizer change onto
        an existing db file: without the porter tokenizer, a search misses singular/
        plural variants (e.g. a query term returns 0 hits even when the index holds
        exact matches of its plural form). If sources_fts wasn't built with the porter
        tokenizer, drop and rebuild it from `sources` (the FTS index is fully derived,
        so this loses nothing)."""
        row = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'sources_fts'"
        ).fetchone()
        if row and "porter" in (row["sql"] or ""):
            return
        self.conn.execute("DROP TABLE IF EXISTS sources_fts")
        self.conn.execute(
            "CREATE VIRTUAL TABLE sources_fts USING fts5(id, title, url, "
            "tokenize='porter unicode61 remove_diacritics 2')"
        )
        self.conn.execute(
            "INSERT INTO sources_fts (id, title, url) SELECT id, title, url FROM sources"
        )

    # --- experiment ledger ------------------------------------------------------

    def record_experiment(self, result: dict, weights: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO experiments (created_at, question, winner, winner_score, "
            "weights_json, result_json) VALUES (?,?,?,?,?,?)",
            (_now(), result["question"], result.get("winner"),
             result.get("winner_score"), json.dumps(weights), json.dumps(result)),
        )
        exp_id = cur.lastrowid
        for arm in result.get("arms", []):
            strat = arm["strategy"]
            rep = arm.get("report") or {}
            self.conn.execute(
                "INSERT INTO arms (experiment_id, strategy_name, prompt, quality_score, "
                "n_sources, notebook_id, task_id, error) VALUES (?,?,?,?,?,?,?,?)",
                (exp_id, strat["name"], strat["prompt"], rep.get("quality_score"),
                 rep.get("n_sources"), arm.get("notebook_id"), arm.get("task_id"),
                 arm.get("error")),
            )
        self.conn.commit()
        return exp_id

    def prior_winners(self, question: str, limit: int = 5) -> list[str]:
        """Winning prompts for the same/similar question, newest first (for M3 reuse)."""
        rows = self.conn.execute(
            "SELECT a.prompt FROM experiments e JOIN arms a "
            "ON a.experiment_id = e.id AND a.strategy_name = e.winner "
            "WHERE e.question = ? ORDER BY e.id DESC LIMIT ?",
            (question, limit),
        ).fetchall()
        return [r["prompt"] for r in rows]

    def latest_winner_score(self, question: str) -> float:
        """Winner score of the most recent experiment FOR THIS QUESTION (not the most
        recent experiment overall). Scoping by question matters: querying the single
        latest experiment across ALL topics and using it only when it happens to match
        this question reports best_score=0.0 for a topic that does have a recorded
        winner, whenever a different topic was experimented on more recently."""
        row = self.conn.execute(
            "SELECT winner_score FROM experiments WHERE question = ? "
            "AND winner_score IS NOT NULL ORDER BY id DESC LIMIT 1",
            (question,),
        ).fetchone()
        return row["winner_score"] if row else 0.0

    def arm_scores(self, strategy_name: str, limit: int = 50) -> list[float]:
        """Quality scores for a strategy across experiments, OLDEST→newest, so a trend
        eval reads them in chronological order. Skips arms that errored (NULL score)."""
        rows = self.conn.execute(
            "SELECT a.quality_score FROM arms a JOIN experiments e ON e.id = a.experiment_id "
            "WHERE a.strategy_name = ? AND a.quality_score IS NOT NULL "
            "ORDER BY e.id ASC LIMIT ?",
            (strategy_name, limit),
        ).fetchall()
        return [r["quality_score"] for r in rows]

    def winner_signal_means(self, question: str) -> dict | None:
        """Signal means of the WINNING arm of the latest experiment for a question,
        read from the stored result JSON. Feeds M3 weight tuning. None if no history."""
        row = self.conn.execute(
            "SELECT result_json FROM experiments WHERE question = ? ORDER BY id DESC LIMIT 1",
            (question,),
        ).fetchone()
        if not row:
            return None
        result = json.loads(row["result_json"])
        winner = result.get("winner")
        for arm in result.get("arms", []):
            if arm["strategy"]["name"] == winner and arm.get("report"):
                return arm["report"].get("signal_means")
        return None

    def experiment_count(self, question: str) -> int:
        r = self.conn.execute(
            "SELECT COUNT(*) AS n FROM experiments WHERE question = ?", (question,)
        ).fetchone()
        return r["n"] if r else 0

    def upsert_topic_profile(self, profile: dict) -> None:
        self.conn.execute(
            "INSERT INTO topic_profiles (topic, best_prompt, best_score, weights_json, "
            "runs, updated_at) VALUES (?,?,?,?,?,?) ON CONFLICT(topic) DO UPDATE SET "
            "best_prompt=excluded.best_prompt, best_score=excluded.best_score, "
            "weights_json=excluded.weights_json, runs=excluded.runs, "
            "updated_at=excluded.updated_at",
            (profile["topic"], profile.get("best_prompt"), profile.get("best_score"),
             json.dumps(profile.get("weights", {})), profile.get("runs", 0), _now()),
        )
        self.conn.commit()

    def get_topic_profile(self, topic: str) -> dict | None:
        row = self.conn.execute(
            "SELECT topic, best_prompt, best_score, weights_json, runs "
            "FROM topic_profiles WHERE topic = ?", (topic,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["weights"] = json.loads(d.pop("weights_json") or "{}")
        return d

    # --- M4 sparring ------------------------------------------------------------

    def record_sparring(self, result: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO sparring (created_at, question, prompt, winner, result_json) "
            "VALUES (?,?,?,?,?)",
            (_now(), result["question"], result["prompt"], result.get("winner_family"),
             json.dumps(result)),
        )
        spar_id = cur.lastrowid
        for r in result.get("results", []):
            rep = r.get("report") or {}
            self.conn.execute(
                "INSERT INTO sparring_families (sparring_id, family, quality_score, "
                "n_sources, available, error) VALUES (?,?,?,?,?,?)",
                (spar_id, r["family"], rep.get("quality_score"), rep.get("n_sources"),
                 1 if r.get("available") else 0, r.get("error")),
            )
        self.conn.commit()
        return spar_id

    def family_leaderboard(self) -> list[dict]:
        """Mean quality per family across all sparring sessions — who finds better
        sources overall. Only scored (available, no-error) rows count."""
        rows = self.conn.execute(
            "SELECT family, AVG(quality_score) AS mean_quality, COUNT(*) AS n "
            "FROM sparring_families WHERE quality_score IS NOT NULL "
            "GROUP BY family ORDER BY mean_quality DESC"
        ).fetchall()
        return [{"family": r["family"], "mean_quality": round(r["mean_quality"], 4),
                 "sessions": r["n"]} for r in rows]

    def list_experiments(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, created_at, question, winner, winner_score "
            "FROM experiments ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_experiment(self, exp_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT result_json FROM experiments WHERE id = ?", (exp_id,)
        ).fetchone()
        return json.loads(row["result_json"]) if row else None

    # --- catalog (M2) -----------------------------------------------------------

    def upsert_notebook(self, nb_id: str, title: str) -> None:
        self.conn.execute(
            "INSERT INTO notebooks (id, title, synced_at) VALUES (?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title, synced_at=excluded.synced_at",
            (nb_id, title, _now()),
        )
        self.conn.commit()

    def upsert_source(self, source_id: str, notebook_id: str, title: str,
                      url: str, domain: str) -> None:
        """Upsert a source row and keep its FTS entry in sync (title+url indexed)."""
        self.conn.execute(
            "INSERT INTO sources (id, notebook_id, title, url, domain, synced_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "notebook_id=excluded.notebook_id, title=excluded.title, url=excluded.url, "
            "domain=excluded.domain, synced_at=excluded.synced_at",
            (source_id, notebook_id, title, url, domain, _now()),
        )
        # external-content FTS: delete-then-insert keeps it consistent on re-sync
        self.conn.execute("DELETE FROM sources_fts WHERE id = ?", (source_id,))
        self.conn.execute(
            "INSERT INTO sources_fts (id, title, url) VALUES (?,?,?)",
            (source_id, title, url),
        )
        self.conn.commit()

    def list_notebooks(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT n.id, n.title, n.synced_at, "
            "(SELECT COUNT(*) FROM sources s WHERE s.notebook_id = n.id) AS n_sources "
            "FROM notebooks n ORDER BY n.synced_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def search_sources(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search titles/urls. Terms are OR'd with a trailing prefix match
        each. FTS5's default is an implicit AND of every term, so a natural two-word
        query returns 0 rows unless a single source matches both words -- one term
        missing kills the whole query. This catalog exists so an agent can check "do
        we already know about X" before spending a real Deep Research run; a false
        "no results" defeats that while an over-broad list doesn't, so OR + prefix
        optimizes for recall."""
        # split on anything that isn't a word character, not just whitespace -- FTS5's
        # query parser treats a bare '-' as NOT, so a hyphenated term like "robo-advisor"
        # left unquoted is parsed as "robo AND NOT advisor*" (one operator, not one
        # token), killing every match. The unicode61 indexer tokenizes the same way on
        # the write side, so splitting here keeps query- and index-side splitting
        # consistent instead of just working around one operator.
        terms = [t for t in re.split(r"\W+", query) if t]
        fts_query = " OR ".join(f'{t}*' for t in terms) if terms else query
        try:
            rows = self.conn.execute(
                "SELECT s.id, s.notebook_id, s.title, s.url, s.domain "
                "FROM sources_fts f JOIN sources s ON s.id = f.id "
                "WHERE sources_fts MATCH ? ORDER BY bm25(sources_fts) LIMIT ?",
                (fts_query, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def close(self) -> None:
        self.conn.close()
