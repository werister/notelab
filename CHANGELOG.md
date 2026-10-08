# Changelog

## Unreleased

### Added
- **Source-kind diversity.** Every source is classified (offline, from its URL/title) as
  `paper · docs · repo · video · discussion · reference · web`. The `diversity` signal now
  blends domain spread with *kind* spread (`diversity_type_weight`, default 0.5), so a mix of
  a paper, a repo, a video and a discussion ranks above many blogs on many domains. Reports
  expose `type_mix`, `type_diversity`, and `domain_diversity`. Video/repo/discussion sources
  sit at a neutral authority baseline instead of being penalized.
- **`project_kickstart` weight preset** — tuned for starting a project from a diverse mix of
  papers, docs, repos, videos and community discussion.

### Changed
- README reframed around the diverse-research/project-kickstart core; the code index is now
  presented as a supporting feature. Trimmed "what we fixed" narration.
- `reliability.py` decoupled from any specific sibling project: the external reliability ledger
  is configurable via `NOTELAB_RELIABILITY_LEDGER` and is a no-op when unset.
- `scripts/repro_results_2026-09-23.py` reads `NOTELAB_REPRO_NOTEBOOK_ID` instead of a hardcoded
  notebook id.

### Removed
- `docs/superpowers/` dev-planning artifacts (contained personal local paths and references to
  a private sibling project).

### Added (earlier in this cycle)
- **Skills** — `research-loop` and `skill-evolution` alongside `nlm-skill`; champion/challenger
  gate (margin + noise floor, cost rule, held-out, green tree).
- `scripts/install-skills.sh` (claude/cursor, `--link`, `--with-guard`) and `scripts/verify.sh`
  (offline install checklist).
- `SECURITY.md`, `SCOPE.md`,
  `docs/NLMX.md` (guard evidence, moved out of the README).
- Guard live evals: 6/6 across two model families.

- **Code index** (`notelab.codeindex`, tools `code_index` / `code_find` / `code_versions` / `code_diff`):
  local, stdlib, zero-token. Exact source for definitions, AST-accurate callers and subclasses, ranked
  text search; every indexed version is kept (content-deduplicated) and diffable.
- `research_ingest_codebase(mode="files")`: ingest the real source files (bundled, with path headers)
  instead of only a structure digest. Default stays `digest`.
- `code-questions` skill.
- `research_fix_videos`: Deep Research imports YouTube links as web pages that can hold a bot wall instead of the
  transcript; this plans or re-adds them natively (`--youtube`) so the real transcript is used. Never deletes unless
  `remove_junk=true`.
- `docs/EVALUATIONS.md`: what is checked, how to rerun it, and the limits.

### Changed
- README rewritten: observed-failure table, three diagrams, per-agent install, try-it prompts.
- Test suite: 353 passed, 3 optional/env-gated tests skipped.

### Fixed
- `brag-output/work/render.py` no longer hardcodes a local path.
- Stripped personal review residue ("sparring review caught this", "found live", "fresh
  third-party code review", named reviewers) from shipped code, the `nlmx` wrapper, tests, and
  docs — keeping the technical rationale.
- `nlmx` guard: `notebook query` no longer refuses a bare `-s` flag. Only `--source-ids` is the
  broken flag on query (it has no short alias), so refusing `-s` wrongly blocked a legitimate query.
- Removed a broken doc reference to a deleted `docs/superpowers/` design file.

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
