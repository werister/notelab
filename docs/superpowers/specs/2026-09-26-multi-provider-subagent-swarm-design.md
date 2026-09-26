# Multi-provider subagent swarm: real per-family dispatch + isolated code-fix races

## Problem

Two related gaps, both already diagnosed in this repo's own docs, neither fixed:

1. **`research_spar` doesn't do what it says.** `ToolLogic.spar` (`src/notelab/_tools.py:179`)
   detects which adversarial CLIs are installed via `detect_runners()`, then ignores that
   entirely and routes every family's actual research through the identical NotebookLM path
   (`self.adapter.research_run(...)`). `family_leaderboard`'s "which family finds the best
   sources" ranking isn't comparing model families — it's comparing repeated identical
   NotebookLM runs, family name attached as cosmetic labeling. `FIELD-NOTES.md` gap #9 already
   names this and explicitly scopes it as "a real feature build, not a bug fix." The
   2026-09-24 Laya spec explicitly deferred it: "Not touching gap #9 ... unrelated, separate
   problem." This spec is that deferred fix.

2. **No live multi-provider capability exists at all for actually writing code.** The
   2026-09-25 handoff inventoried five real, working CLI-agent tools with real credits
   (opencode, muse/meta, cline, kilo, kiro-cli) but nothing in either repo dispatches a real
   task to more than one of them concurrently, isolates their edits from each other, or
   records genuine outcomes. `record_agent_session_v2.py` only records *retrospective*,
   hand-entered numbers about past sessions — it never calls any of these tools itself.

## Non-goals

- Not building a general-purpose "run any task on any agent" platform. Scoped to: (a) fixing
  `spar()`'s real dispatch for research comparison, and (b) one concrete, isolated, reviewed
  code-fix race as a first real proof this works end-to-end.
- Not auto-merging anything. Every agent's code output lands as a diff on its own branch in
  its own worktree; a human (via this session) reviews before anything reaches `main`.
- Not touching `codex`/`qoder`/`copilot`/`qwen` runner definitions — out of scope, already
  documented as blocked (codex, qoder) or not part of this user's working fleet (copilot,
  qwen were speculative entries, never confirmed installed here).
- Not building the always-on dashboard (task 3) — this spec only ensures real dispatch data
  lands in the existing `redacted_private_project.routing_monitor` ledger schema in a shape that dashboard
  can later consume. The dashboard itself is separate, future work.
- Not fixing FIELD-NOTES gaps #3, #4, #5, #7 — only #9 (this spec) and #2 (the race target,
  see below). Each remaining gap is its own future scoped change.

## Design — Component A: `spar()` real per-family dispatch

**`evals.py` — extend `KNOWN_RUNNERS`** with the four missing tools from the user's actual
working fleet, using verified non-interactive invocations (checked live via `--help` this
session, not guessed):

```python
RunnerSpec("muse", "muse", ["exec", "--workspace", "{cwd}", "--yolo", "{prompt}"]),
RunnerSpec("cline", "cline", ["--cwd", "{cwd}", "{prompt}"]),
RunnerSpec("kilo", "kilo", ["run", "--dir", "{cwd}", "--auto", "{prompt}"]),
RunnerSpec("kiro-cli", "kiro-cli", ["chat", "{prompt}", "--no-interactive", "-a"]),
```

This introduces a second placeholder, `{cwd}`, alongside the existing `{prompt}` — needed
because `muse`/`cline`/`kilo` take their target directory as an explicit flag rather than
inheriting `subprocess.run`'s `cwd=` kwarg reliably in every mode. `run_adversary`'s
`args.replace()` loop extends to substitute both. `opencode`, `claude`, `codex`, `qoder`,
`qwen`'s existing templates have no `{cwd}` placeholder, so `run_adversary` must pass
`cwd=cwd` to `subprocess.run` unconditionally (it already accepts a `cwd` param, currently
unused by `spar()`) — this covers both styles without special-casing per tool.

**`_tools.py` — fix `spar()`'s `family_runner`** to call `run_adversary(spec, p, cwd=...)`
per family instead of `self.adapter.research_run(...)`:

```python
def family_runner(family: str, p: str):
    spec = next((r for r in detect_runners() if r.name == family), None)
    if spec is None or not spec.available:
        return [], {"available": False, "error": f"{family} not installed"}
    result = run_adversary(spec, p, timeout=180)
    if result.timed_out:
        return [], {"available": True, "error": "timed out"}
    sources = _sources_from_adversary_output(result, question=p)
    return sources, {"available": True, "error": result.stderr[:300] if result.returncode else None}
```

**New: `_sources_from_adversary_output()`** (in `sparring.py`, alongside `SparringSession` —
it's specifically sparring's concern, not a general evals utility). Extracts URLs from the
CLI's raw stdout via regex (`https?://\S+`), wraps each as a `SourceFeatures` with the
URL and a bounded snippet of surrounding text as `content`. If zero URLs are found, wraps
the *entire* stdout as a single sourceless `SourceFeatures` (empty `url`) so a family that
answered without explicit citations is still scored on content quality by the existing
`score_sources()` rather than getting silently zeroed out (an agent CLI answering a research
question in prose, no links, is a real — if weaker — outcome, not a non-event).

## Design — Component B: isolated code-fix dispatch

A new example/script, `examples/dispatch_code_fix.py` ([redacted-private-project], since it owns the ledger),
following `superpowers:using-git-worktrees`:

1. For each of the 5 agents, create an isolated git worktree off the target repo
   (`notelab`) on a fresh branch: `swarm/<agent>/<task-slug>`.
2. Invoke that agent's CLI via the *same* `run_adversary`-style mechanism as Component A,
   but with a code-editing prompt (task description + acceptance criteria) instead of a
   research question, `cwd` pointed at that agent's worktree, and no `{prompt}`-only research
   framing — the prompt explicitly asks for a code change plus a one-line summary of what
   changed.
3. After each run: `git -C <worktree> diff` captures what actually changed (empty diff is a
   real, recorded outcome — "ran, produced nothing" — not an error).
4. Real observed token/latency data (where the CLI's `--json`/`--format json` output exposes
   it — opencode, cline, muse's `--json` all do; kilo/kiro-cli fall back to wall-clock latency
   only, recorded honestly as `total_tokens=0` with a note, not estimated) gets recorded via
   `LedgerStore`/`Session.record_step()`, same schema `record_agent_session_v2.py` already
   establishes — `routed_model` = the agent name, `provider_name` = the CLI, `accepted`
   determined by whether the diff applies cleanly AND the existing offline test suite still
   passes against it (`pytest -q` inside the worktree with the diff applied).
5. Nothing merges automatically. The script's output is a per-agent report (diff, whether
   tests passed, tokens/latency where known) for human review in this session — I present the
   results and recommend which (if any) diff to actually merge.

## First real run: FIELD-NOTES gap #2, all 5 agents

**Target:** `docs/FIELD-NOTES.md` gap #2 — "No live model-id resolution → stale ids 404
silently as 'no access'" — scoped specifically to `src/notelab/research/semantic.py`'s
hardcoded `MISTRAL_EMBED_MODEL`, the only live chat/embedding-API call site in notelab's own
source (checked this session — no other provider model-id is called directly from Python;
`evals.py`'s adversarial runners shell out to CLIs, they don't call chat-completion APIs).

**Exact task prompt given to all 5 agents, verbatim, identical:**

> In `src/notelab/research/semantic.py`, before calling the Mistral embeddings endpoint,
> resolve `MISTRAL_EMBED_MODEL` against a live `GET https://api.mistral.ai/v1/models` call and
> raise a clear, distinct error if the configured model id isn't in the returned list —
> distinguishing "model not found" from an auth/network failure, rather than only discovering
> a stale id via a possibly-misleading HTTP error on the embeddings call itself. Add a test
> using a mocked `urllib` response (no real network/API key needed to run it). Keep the
> existing contract: `SemanticExpansionError` on any failure, never a silent empty result.

**Chosen because:** fully offline-verifiable (mocked HTTP, no live Mistral key needed —
matches the "126 offline tests, no account needed" guarantee), single real in-repo locus,
precise acceptance criteria (a test the diff must make pass), small enough for 5 one-shot
runs to be a fair comparison rather than a coin-flip on scope interpretation. Gap #5 was
doc-only (not a code race). Gap #3's locus isn't in notelab's own code. Gap #4 needs a live
NotebookLM account to verify — not fair-race material.

## Testing

- Component A: unit tests for `_sources_from_adversary_output()` (URL-found and no-URL-found
  cases) and an updated `spar()` test that injects a fake `run_adversary` and asserts each
  family's distinct output is attributed correctly (catches any regression back to "everyone
  gets the same result").
- Component B: `dispatch_code_fix.py` itself gets a smoke test with all 5 `run_adversary`
  calls mocked (no real CLI invocation, no real cloud spend, in CI) plus one real live run
  in this session (the gap #2 race) as the actual verification.
- The gap #2 fix itself: each agent's diff is accepted only if `pytest -q` passes inside its
  worktree with the diff applied — this is the objective bar, not agent self-report.

## Open questions

1. Per-agent timeout for the code-fix race — 180s (same as Component A) may be too short for
   a real multi-file code change with test-writing. Will adjust per observed behavior during
   the live run rather than guess up front.
2. If two or more agents produce equally-valid diffs, which gets recommended for merge is a
   judgment call made after seeing real results, not decided in advance.
