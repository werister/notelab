# Contributing

Thanks for looking at `nlm-research-mcp`. It aims to stay small, transparent, and
runnable on a laptop with no NotebookLM account.

## Ground rules

- **The engine is stdlib-only.** `research/`, `storage.py`, and the tests must import
  nothing outside the standard library. The MCP transport (`mcp`) is an *optional*
  extra so `pip install .` + `pytest` works offline. Don't add a runtime dependency to
  the engine.
- **Tests are offline.** No network, no NotebookLM, no external agent CLI in CI. Use
  fixture `SourceFeatures` and fake runners (see `tests/`). A test that needs the
  network doesn't belong in the suite.
- **Scores stay auditable.** Every quality/eval result must decompose into its inputs.
  No black-box verdicts — if you add a signal, expose it in the report `as_dict()`.
- **Destructive ops stay gated.** Source eviction is a *plan*, applied by the caller
  behind NotebookLM's confirm gate. Don't add a code path that deletes sources silently.

## Dev setup

```bash
pip install -e ".[dev]"     # or: uv run --with pytest pytest
pytest -q                   # offline
python examples/self_improving_loop.py   # see the whole loop, no account needed
```

The suite reports `N passed, 2 skipped`: those two skips are
`test_live_semantic_beats_lexical_coverage_on_synonym_heavy_fixture` (a live network
test gated on a real `MISTRAL_API_KEY`) and the live Laya ablation test (gated on
`laya-mlx`, `pip install 'notelab[laya]'`). Both are expected to skip in CI and on a
fresh checkout (see `-rs` output for the reasons) — they aren't broken tests.

## Adding an adversarial runner

Append a `RunnerSpec` to `KNOWN_RUNNERS` in `research/evals.py` with the binary name
and a one-shot args template using `{prompt}`. Detection and graceful-skip are automatic.

## Before you push (and before the repo goes public)

`python scripts/scan_leaks.py` checks tracked files for key/token shapes, home-directory paths, real
email addresses, private IPs and notebook-image URLs; `--history` also checks every added line,
commit message and author across remote branches and PR refs. Put personal terms (your username,
name, hostnames) one per line in `.local/leak-terms.txt` (git-ignored) and they are checked too.
`tests/test_no_leaks.py` runs the generic scan in CI.

## PRs

Keep them focused, add a test for behaviour you change, and run `pytest -q` before
pushing. Milestones are tracked in `docs/DESIGN.md`.
