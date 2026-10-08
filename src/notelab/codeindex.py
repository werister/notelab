"""Local, zero-token code index: exact symbol source, full-text search, callers.

NotebookLM is good at prose and bad at exact code (it reflows whitespace and is slow). Grep is
exact but dumb. This sits between: index repos once (stdlib only, SQLite FTS5), then answer
targeted questions in milliseconds with the real source, file and line, no model call:

    def      -> where is X defined, with its exact source
    callers  -> every call site of X (Python: AST-accurate; other languages: regex)
    subclasses -> classes that directly inherit from X (Python)
    text     -> full-text search ranked by relevance, as file:line snippets

Versions are kept, never overwritten: every `index` run stores a snapshot labelled by git
commit (or --version), file contents are deduplicated by hash, and you can query any past
version or diff two. "How did X look at v1.2?" and "what changed since?" stay answerable.

CLI:  python -m notelab.codeindex index <repo> [--name N] [--version V]
      python -m notelab.codeindex find <query> [--kind any|def|callers|text] [--repo N] [--version V|*]
      python -m notelab.codeindex versions [--repo N]
      python -m notelab.codeindex diff <repo> <old> <new> [--summary]
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

DEFAULT_DB = Path.home() / ".local/state/nlm-research-mcp/code.db"
MAX_SOURCE_LINES = 60       # a def longer than this is cut, with a visible note
WINDOW = 40                 # text chunk size for non-Python / module-level lines

_SCHEMA = """
CREATE TABLE IF NOT EXISTS blobs(hash TEXT PRIMARY KEY, content TEXT);
CREATE TABLE IF NOT EXISTS versions(repo TEXT, version TEXT, files INT, symbols INT,
                                    seq INTEGER PRIMARY KEY AUTOINCREMENT, UNIQUE(repo, version));
CREATE TABLE IF NOT EXISTS files(repo TEXT, version TEXT, path TEXT, lang TEXT, hash TEXT,
                                 PRIMARY KEY(repo, version, path));
CREATE TABLE IF NOT EXISTS symbols(repo TEXT, version TEXT, path TEXT, qualname TEXT, name TEXT,
                                   kind TEXT, line INT, end_line INT);
CREATE INDEX IF NOT EXISTS symbols_name ON symbols(name);
CREATE TABLE IF NOT EXISTS bases(repo TEXT, version TEXT, path TEXT, qualname TEXT, base TEXT, line INT);
CREATE INDEX IF NOT EXISTS bases_base ON bases(base);
CREATE TABLE IF NOT EXISTS calls(repo TEXT, version TEXT, path TEXT, line INT, name TEXT, caller TEXT);
CREATE INDEX IF NOT EXISTS calls_name ON calls(name);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    repo UNINDEXED, version UNINDEXED, path UNINDEXED, line UNINDEXED, end_line UNINDEXED, text,
    tokenize="unicode61 tokenchars '_'");
"""

# Regex symbol finders for non-Python languages: (pattern, kind). Group 1 is the name.
_GENERIC = [
    (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)"), "function"),
    (re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?(?:class|interface|struct|enum|trait|type)\s+(\w+)"), "class"),
    (re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(\w+)"), "function"),
    (re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)"), "function"),
    (re.compile(r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>"), "function"),
    (re.compile(r"^\s*def\s+(?:self\.)?(\w+[?!]?)"), "function"),
]
_CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
_NOT_CALLS = {"if", "for", "while", "switch", "catch", "return", "function", "def", "fn", "func", "class", "elif"}


def _py_symbols(path: str, content: str):
    """(symbols, calls, bases) from the AST. symbols: (qualname, name, kind, line, end);
    calls: (line, name, caller); bases: (class qualname, base name, line)."""
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return None
    syms: list[tuple] = []
    calls: list[tuple] = []
    bases: list[tuple] = []

    def visit(node, stack):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(ch, ast.ClassDef) else (
                    "method" if stack and stack[-1][1] == "class" else "function")
                start = min([ch.lineno] + [d.lineno for d in ch.decorator_list])
                q = ".".join([s[0] for s in stack] + [ch.name])
                syms.append((q, ch.name, kind, start, ch.end_lineno or ch.lineno))
                if isinstance(ch, ast.ClassDef):
                    for b in ch.bases:
                        bn = b.id if isinstance(b, ast.Name) else b.attr if isinstance(b, ast.Attribute) else None
                        if bn:
                            bases.append((q, bn, ch.lineno))
                visit(ch, stack + [(ch.name, "class" if kind == "class" else "fn")])
            else:
                if isinstance(ch, ast.Call):
                    f = ch.func
                    nm = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
                    if nm:
                        calls.append((ch.lineno, nm, ".".join(s[0] for s in stack) or "<module>"))
                visit(ch, stack)

    visit(tree, [])
    return syms, calls, bases


def _generic_symbols(content: str):
    lines = content.splitlines()
    found = []
    for i, ln in enumerate(lines, 1):
        for rx, kind in _GENERIC:
            m = rx.match(ln)
            if m:
                found.append([m.group(1), m.group(1), kind, i, i])
                break
    for k, s in enumerate(found):  # end = line before the next symbol, capped
        nxt = found[k + 1][3] - 1 if k + 1 < len(found) else len(lines)
        s[4] = max(s[3], min(nxt, s[3] + MAX_SOURCE_LINES))
    calls = []
    cur = "<module>"
    owners = {s[3]: s[0] for s in found}
    for i, ln in enumerate(lines, 1):
        cur = owners.get(i, cur)
        for m in _CALL_RE.finditer(ln):
            if m.group(1) not in _NOT_CALLS and m.group(1) != cur:
                calls.append((i, m.group(1), cur))
    return [tuple(s) for s in found], calls, []


def _chunks(content: str, syms):
    """(line, end, text): one chunk per def, plus WINDOW-line chunks for everything else."""
    lines = content.splitlines()
    covered = [False] * (len(lines) + 2)
    out = []
    for _q, _n, kind, a, b in syms:
        if kind != "class":
            out.append((a, b, "\n".join(lines[a - 1:b])))
            for i in range(a, b + 1):
                covered[i] = True
    i = 1
    while i <= len(lines):
        if covered[i]:
            i += 1
            continue
        j = i
        while j <= len(lines) and not covered[j] and j - i < WINDOW:
            j += 1
        text = "\n".join(lines[i - 1:j - 1])
        if text.strip():
            out.append((i, j - 1, text))
        i = j
    return out


def detect_version(repo: str) -> str:
    """Git short SHA of a local repo (+ '-dirty' if uncommitted changes), else 'latest'."""
    try:
        sha = subprocess.run(["git", "-C", repo, "rev-parse", "--short", "HEAD"], capture_output=True,
                             text=True, timeout=10)
        if sha.returncode != 0:
            return "latest"
        dirty = subprocess.run(["git", "-C", repo, "status", "--porcelain"], capture_output=True,
                               text=True, timeout=10).stdout.strip()
        return sha.stdout.strip() + ("-dirty" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "latest"


class CodeIndex:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.executescript(_SCHEMA)

    def index(self, repo: str, name: str | None = None, version: str | None = None,
              max_files: int = 5000) -> dict:
        """Index a local path or GitHub URL as a new version. Same (name, version) is replaced;
        other versions are kept."""
        from .research.codebase_ingest import CodebaseIngester
        digest = CodebaseIngester(max_files=max_files, max_file_lines=1_000_000).ingest(repo)
        name = name or digest.repo_name
        version = version or (detect_version(repo) if Path(repo).exists() else "latest")
        for t in ("files", "symbols", "calls", "bases", "chunks", "versions"):
            self.db.execute(f"DELETE FROM {t} WHERE repo=? AND version=?", (name, version))
        n_sym = 0
        for f in digest.files:
            h = hashlib.sha256(f.content.encode("utf-8", "replace")).hexdigest()[:16]
            self.db.execute("INSERT OR IGNORE INTO blobs VALUES(?,?)", (h, f.content))
            self.db.execute("INSERT INTO files VALUES(?,?,?,?,?)", (name, version, f.path, f.language, h))
            res = _py_symbols(f.path, f.content) if f.path.endswith(".py") else None
            if res is None and f.language not in ("Markdown", "TOML", "YAML", "JSON", "Other", "Shell"):
                res = _generic_symbols(f.content)
            syms, calls, bases = res or ([], [], [])
            self.db.executemany("INSERT INTO bases VALUES(?,?,?,?,?,?)",
                                [(name, version, f.path, *b) for b in bases])
            self.db.executemany("INSERT INTO symbols VALUES(?,?,?,?,?,?,?,?)",
                                [(name, version, f.path, *s) for s in syms])
            self.db.executemany("INSERT INTO calls VALUES(?,?,?,?,?,?)",
                                [(name, version, f.path, *c) for c in calls])
            self.db.executemany("INSERT INTO chunks VALUES(?,?,?,?,?,?)",
                                [(name, version, f.path, a, b, t) for a, b, t in _chunks(f.content, syms)])
            n_sym += len(syms)
        self.db.execute("INSERT INTO versions(repo,version,files,symbols) VALUES(?,?,?,?)",
                        (name, version, len(digest.files), n_sym))
        self.db.commit()
        return {"repo": name, "version": version, "files": len(digest.files), "symbols": n_sym}

    def repos(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT DISTINCT repo FROM versions ORDER BY 1")]

    def versions(self, repo: str | None = None) -> list[dict]:
        q, a = ("SELECT repo,version,files,symbols FROM versions" + (" WHERE repo=?" if repo else "")
                + " ORDER BY repo, seq"), ((repo,) if repo else ())
        return [dict(zip(("repo", "version", "files", "symbols"), r)) for r in self.db.execute(q, a)]

    def _latest(self, repo: str) -> str | None:
        r = self.db.execute("SELECT version FROM versions WHERE repo=? ORDER BY seq DESC LIMIT 1", (repo,)).fetchone()
        return r[0] if r else None

    def _scope(self, repo: str | None, version: str | None):
        """SQL fragment + params restricting rows to (repo, version). Default: newest version of each repo."""
        if version == "*":
            return ((" AND repo=?", (repo,)) if repo else ("", ()))
        repos = [repo] if repo else self.repos()
        pairs = [(r, version or self._latest(r)) for r in repos]
        pairs = [p for p in pairs if p[1]]
        if not pairs:
            return " AND 0", ()
        return (" AND (" + " OR ".join("(repo=? AND version=?)" for _ in pairs) + ")",
                tuple(x for p in pairs for x in p))

    def _source(self, repo, version, path, a, b):
        row = self.db.execute("SELECT content FROM files f JOIN blobs b ON b.hash=f.hash "
                              "WHERE f.repo=? AND f.version=? AND f.path=?", (repo, version, path)).fetchone()
        lines = row[0].splitlines()[a - 1:b]
        cut = len(lines) > MAX_SOURCE_LINES
        body = "\n".join(lines[:MAX_SOURCE_LINES])
        return body + (f"\n... ({b - a + 1 - MAX_SOURCE_LINES} more lines)" if cut else "")

    def find(self, query: str, kind: str = "any", repo: str | None = None, limit: int = 5,
             version: str | None = None) -> list[dict]:
        rp, ra = self._scope(repo, version)
        out: list[dict] = []
        q = query.strip()
        short = q.split(".")[-1].split("(")[0].strip()
        if kind in ("any", "def"):
            rows = self.db.execute(
                "SELECT repo,version,path,qualname,kind,line,end_line FROM symbols WHERE "
                "(name=? OR qualname=? OR qualname LIKE ?)" + rp +
                " ORDER BY (name=? OR qualname=?) DESC, length(qualname) LIMIT ?",
                (short, q, "%." + q, *ra, short, q, limit)).fetchall()
            for r, v, p, qn, k, a, b in rows:
                out.append({"type": "def", "repo": r, "version": v, "path": p, "line": a, "end_line": b,
                            "kind": k, "name": qn, "text": self._source(r, v, p, a, b)})
        if kind == "callers":
            rows = self.db.execute(
                "SELECT repo,version,path,line,caller FROM calls WHERE name=?" + rp +
                " ORDER BY repo,path,line LIMIT ?", (short, *ra, max(limit, 25))).fetchall()
            for r, v, p, ln, caller in rows:
                out.append({"type": "call", "repo": r, "version": v, "path": p, "line": ln, "end_line": ln,
                            "name": caller, "text": self._source(r, v, p, ln, ln).strip()})
        if kind == "subclasses":
            rows = self.db.execute(
                "SELECT repo,version,path,qualname,line FROM bases WHERE base=?" + rp +
                " ORDER BY repo,path,line LIMIT ?", (short, *ra, max(limit, 25))).fetchall()
            for r, v, p, qn, ln in rows:
                out.append({"type": "subclass", "repo": r, "version": v, "path": p, "line": ln, "end_line": ln,
                            "name": qn, "text": self._source(r, v, p, ln, ln).strip()})
        if kind in ("any", "text") and (kind == "text" or not out):
            words = re.findall(r"\w+", q)
            rows = []
            for joiner in (" AND ", " OR "):
                if not words:
                    break
                rows = self.db.execute(
                    "SELECT repo,version,path,line,end_line,text FROM chunks WHERE chunks MATCH ?" + rp +
                    " ORDER BY bm25(chunks) LIMIT ?",
                    (joiner.join(f'"{w}"' for w in words), *ra, limit)).fetchall()
                if rows:
                    break
            for r, v, p, a, b, t in rows:
                out.append({"type": "text", "repo": r, "version": v, "path": p, "line": a, "end_line": b,
                            "kind": "chunk", "name": "", "text": "\n".join(t.splitlines()[:25])})
        return out

    def _symbol_hashes(self, repo: str, version: str) -> dict[tuple[str, str], str]:
        out = {}
        rows = self.db.execute("SELECT path,qualname,line,end_line FROM symbols WHERE repo=? AND version=? "
                               "AND kind!='class'", (repo, version)).fetchall()
        for path, qn, a, b in rows:
            src = self._source(repo, version, path, a, b)
            out[(path, qn)] = hashlib.sha256(src.encode()).hexdigest()[:12]
        return out

    def diff(self, repo: str, old: str, new: str) -> dict:
        """Functions/methods added, removed and changed between two indexed versions."""
        vs = {v["version"] for v in self.versions(repo)}
        for v in (old, new):
            if v not in vs:
                raise ValueError(f"{repo}@{v} is not indexed (have: {', '.join(sorted(vs)) or 'none'})")
        a, b = self._symbol_hashes(repo, old), self._symbol_hashes(repo, new)
        fa = {r[0]: r[1] for r in self.db.execute("SELECT path,hash FROM files WHERE repo=? AND version=?", (repo, old))}
        fb = {r[0]: r[1] for r in self.db.execute("SELECT path,hash FROM files WHERE repo=? AND version=?", (repo, new))}
        return {"repo": repo, "old": old, "new": new,
                "files_added": sorted(set(fb) - set(fa)), "files_removed": sorted(set(fa) - set(fb)),
                "files_changed": sorted(p for p in set(fa) & set(fb) if fa[p] != fb[p]),
                "symbols_added": sorted(f"{p}::{q}" for p, q in set(b) - set(a)),
                "symbols_removed": sorted(f"{p}::{q}" for p, q in set(a) - set(b)),
                "symbols_changed": sorted(f"{p}::{q}" for (p, q) in set(a) & set(b) if a[(p, q)] != b[(p, q)])}


def render(results: list[dict]) -> str:
    if not results:
        return "no matches"
    parts = []
    for r in results:
        head = f"{r['repo']}@{r['version']}/{r['path']}:{r['line']}" + (f"-{r['end_line']}" if r["end_line"] != r["line"] else "")
        label = {"def": f"{r.get('kind', 'def')} {r['name']}", "call": f"call in {r['name']}", "subclass": f"subclass {r['name']}",
                 "text": "match"}[r["type"]]
        parts.append(f"{head}  [{label}]\n{r['text']}")
    return "\n\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="notelab-code", description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(DEFAULT_DB))
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("index"); pi.add_argument("repo"); pi.add_argument("--name"); pi.add_argument("--version")
    pf = sub.add_parser("find"); pf.add_argument("query")
    pf.add_argument("--kind", default="any", choices=["any", "def", "callers", "subclasses", "text"])
    pf.add_argument("--repo"); pf.add_argument("--version"); pf.add_argument("--limit", type=int, default=5)
    pv = sub.add_parser("versions"); pv.add_argument("--repo")
    pd = sub.add_parser("diff"); pd.add_argument("repo"); pd.add_argument("old"); pd.add_argument("new")
    pd.add_argument("--summary", action="store_true", help="counts only")
    a = ap.parse_args(argv)
    ci = CodeIndex(a.db)
    if a.cmd == "index":
        print(ci.index(a.repo, a.name, a.version))
    elif a.cmd == "versions":
        print("\n".join(f"{v['repo']}@{v['version']}  {v['files']} files, {v['symbols']} symbols"
                         for v in ci.versions(a.repo)) or "(none indexed)")
    elif a.cmd == "diff":
        import json
        d = ci.diff(a.repo, a.old, a.new)
        if a.summary:
            print(f"{d['repo']} {d['old']} -> {d['new']}\n"
                  f"files:     +{len(d['files_added'])} ~{len(d['files_changed'])} -{len(d['files_removed'])}\n"
                  f"functions: +{len(d['symbols_added'])} ~{len(d['symbols_changed'])} -{len(d['symbols_removed'])}")
        else:
            print(json.dumps(d, indent=2))
    else:
        print(render(ci.find(a.query, a.kind, a.repo, a.limit, a.version)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
