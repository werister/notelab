# Changelog

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
