# Changelog

## Unreleased

### Added
- **Skills** — `research-loop` and `skill-evolution` alongside `nlm-skill`; champion/challenger
  gate (margin + noise floor, cost rule, held-out, green tree).
- `scripts/install-skills.sh` (claude/cursor, `--link`, `--with-guard`) and `scripts/verify.sh`
  (offline install checklist).
- `SECURITY.md`, `SCOPE.md`, `docs/SECURITY-LOG.md` (publish-audit checklist + entries),
  `docs/NLMX.md` (guard evidence, moved out of the README).
- Guard live evals: 6/6 across two model families.

- **Code index** (`notelab.codeindex`, tools `code_index` / `code_find` / `code_versions` / `code_diff`):
  local, stdlib, zero-token. Exact source for definitions, AST-accurate callers and subclasses, ranked
  text search; every indexed version is kept (content-deduplicated) and diffable.
- `research_ingest_codebase(mode="files")`: ingest the real source files (bundled, with path headers)
  instead of only a structure digest. Default stays `digest`.
- `code-questions` skill; `scripts/bench_code.py` and `scripts/bench_corpus.py`.

### Changed
- README rewritten: observed-failure table, three diagrams, per-agent install, try-it prompts.
- Test suite: 344 passed, 2 optional live tests skipped.

### Fixed
- `brag-output/work/render.py` no longer hardcodes a local path.

## v0.1 — NoteLab

First public cut: a self-improving research MCP server on top of Google NotebookLM,
consolidated from the `nlm-agent-guard` base plus the full research engine.

### Added
- **Guarded base** — `nlmx` wrapper intercepting 3 confirmed NotebookLM corruption
  bugs (non-idempotent import, broken `--source-ids`, missing `--confirm`), plus the
  `nlm-skill` agent files.
- **Quality scoring** — deterministic 5-signal source data-quality scorer with named
  presets (`news`, `lit_review`, `docs`, `balanced`).
- **Prompt A/B** — rank research prompt strategies by the quality of the sources they
  retrieve; record every experiment to a SQLite ledger.
- **Self-evolving sources** — create / merge / skip / evict + budget, with decay so
  stale sources fade; execution is confirm-gated.
- **Adversarial runners + evals** — pluggable agent-CLI runners (opencode, copilot, …)
  and an OLS-slope trend that flags a strategy improving vs. regressing.
- **Strategy memory (M3)** — prompt mutation/exploration + per-topic tuned weights.
- **Multi-model sparring (M4)** — families spar over one corpus, attributed per family,
  with a leaderboard.
- **Catalog** — a local, full-text index of notebooks + sources ("what do we already
  know about X" before researching).
- **Bundled MCP server** — stdlib-only JSON-RPC stdio server; 14 tools; zero
  third-party deps to run. Optional official-SDK path via `--sdk`.
- 67 offline tests, CI on py3.10/3.12/3.13, MIT license, design + config docs, demo.
