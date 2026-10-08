---
name: code-questions
description: "Answer targeted questions about code across one or more repos, and across versions: where is X defined (exact source), who calls X, what subclasses X, ranked full-text search, and what changed between two versions. Local, instant, no model tokens, verbatim. Use when asked 'where is', 'who calls', 'show me the exact code of', 'what subclasses', 'what changed between versions', or to explore an unfamiliar or imported repo. Triggers on: find definition, find callers, subclasses, code search, what changed since, diff versions, explore repo, imported repo."
version: "1.0.0"
---

# Code questions (notelab code index)

Index a repo once, then ask exact questions. Every answer is real source with `repo@version/path:line`.
No model call, no NotebookLM account needed.

## Use

```bash
python3 -m notelab.codeindex index <path-or-github-url> [--name N] [--version V]   # version defaults to git commit
python3 -m notelab.codeindex find <symbol> --kind def                 # where defined + exact source
python3 -m notelab.codeindex find <symbol> --kind callers             # every call site (Python: AST-accurate)
python3 -m notelab.codeindex find <Class> --kind subclasses           # direct subclasses
python3 -m notelab.codeindex find "words to search" --kind text       # ranked full-text, as file:line snippets
python3 -m notelab.codeindex versions                                 # every kept version
python3 -m notelab.codeindex diff <repo> <old> <new>                  # files + functions added/removed/changed
```

The same operations exist as MCP tools: `code_index`, `code_find`, `code_versions`, `code_diff`.
Add `--version V` (or `'*'` for all) to ask about a specific snapshot. The default version is the
**most recently indexed** one — note this is insertion order, not commit recency, so if you backfill an
older commit *after* indexing a newer one, pass `--version` explicitly to avoid querying the older snapshot
by accident.

## Rules

- **Index before you ask.** `find` on an empty index says so; it never guesses.
- **Re-index when code changes.** A new commit gets a new kept version; the old one stays queryable. Re-indexing the same version replaces it.
- **Quote, don't reconstruct.** Return the indexed source verbatim with its `path:line`. If `find` says "no matches", say the symbol was not found; do not answer from memory.
- **Pick the right tool for the question.**
  - Exact/targeted (definition, callers, subclasses, diff): this index.
  - Small repo you can grep: grep is just as good, use whichever is handier.
  - Conceptual / cross-document / prose-heavy ("how does the design hang together", papers + issues + docs): ingest with `research_ingest_codebase(repo, mode="files")` and ask the notebook.
- **`research_ingest_codebase` default mode is a structure digest with no source.** It cannot answer detail questions. Use `mode="files"` for a notebook that can.
