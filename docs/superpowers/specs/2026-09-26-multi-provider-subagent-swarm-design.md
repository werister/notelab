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

**Revised after eng review** (see "Revision after eng + devex review" below): the original
draft gave Component A the same full-trust, write-capable flags (`--yolo`, `--auto`,
`-a`) as Component B, with no worktree isolation to match — a real gap, since `spar()` is
a live MCP tool callable far more casually than a one-off code race. Fixed by removing the
dangerous flags entirely rather than adding isolation to match them: **`spar()` only ever
asks a research question, never asks an agent to edit anything, so it never needs write
trust in the first place.** No isolation is needed for a capability that was never granted.

**`evals.py` — extend `KNOWN_RUNNERS`** with the four missing tools from the user's actual
working fleet, using verified non-interactive invocations (checked live via `--help` this
session, not guessed) — **conservative/default permission mode, no auto-approve or trust
flags**:

```python
RunnerSpec("muse", "muse", ["exec", "{prompt}"]),
    # default --approval-mode on-request: headless + no one to approve => any tool call
    # (including a write) is denied, not silently granted. Fails safe.
RunnerSpec("cline", "cline", ["--auto-approve", "false", "{prompt}"]),
    # cline defaults --auto-approve to true; explicitly override for the research lane.
RunnerSpec("kilo", "kilo", ["run", "{prompt}"]),
    # --auto omitted: defaults to false (write permissions not auto-granted).
RunnerSpec("kiro-cli", "kiro-cli", ["chat", "{prompt}", "--no-interactive"]),
    # -a/--trust-all-tools omitted: no blanket tool trust.
```

No `{cwd}` placeholder here — Component A doesn't need one. `run_adversary` already accepts
a `cwd` kwarg (existing, previously unused by `spar()`); pass `cwd=<repo_root>` as a plain
`subprocess.run` argument so agents get real repo context to ground their research answer,
without any CLI-flag-level directory targeting. `KNOWN_RUNNERS`'s existing docstring promise
("adding another is ~1 line") stays true for this lane — no second placeholder, no special
per-tool wiring, exactly the reuse-ladder shape the file already has for `opencode`/`claude`/
`codex`/`qoder`/`qwen`.

**`_tools.py` — fix `spar()`'s `family_runner`** to call `run_adversary(spec, p, cwd=repo_root,
timeout=180)` per family instead of `self.adapter.research_run(...)`:

```python
def family_runner(family: str, p: str):
    spec = next((r for r in detect_runners() if r.name == family), None)
    if spec is None or not spec.available:
        return [], {"available": False, "error": f"{family} not installed"}
    result = run_adversary(spec, p, timeout=180, cwd=_notelab_repo_root())
    if result.timed_out:
        return [], {"available": True, "error": "timed out (grandchildren killed, see evals.py fix below)"}
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
following `superpowers:using-git-worktrees`. **This is the only place full-trust flags
appear**, and worktree isolation exists specifically to bound them:

```python
CODE_FIX_RUNNERS = [
    RunnerSpec("opencode", "opencode", ["run", "--dir", "{cwd}", "{prompt}"]),
    RunnerSpec("muse", "muse", ["exec", "--workspace", "{cwd}", "--yolo", "{prompt}"]),
    RunnerSpec("cline", "cline", ["--cwd", "{cwd}", "{prompt}"]),  # auto-approve defaults true
    RunnerSpec("kilo", "kilo", ["run", "--dir", "{cwd}", "--auto", "{prompt}"]),
    RunnerSpec("kiro-cli", "kiro-cli", ["chat", "{prompt}", "--no-interactive", "-a"]),
]
```

A deliberately **separate table from Component A's `KNOWN_RUNNERS`**, not a shared/merged
one — different trust level, different purpose, and it keeps `KNOWN_RUNNERS`'s "~1 line to
add a provider" docstring promise true for the research lane instead of silently breaking it
(the eng review's finding: mixing a `{cwd}`-only-some-runners-need-it convention into the
shared table was the actual source of the crash-bug risk, not just a missing kwarg).

**`run_adversary` (`evals.py`) gets two fixes, both benefit Component A too:**

1. **`{cwd}` substitution, properly guarded.** Extend the `args.replace()` loop to also
   substitute `{cwd}` when `cwd` is passed — but only if the template actually contains the
   placeholder; a template with `{cwd}` and no `cwd=` argument is a caller bug, returned as a
   recorded `RunnerResult(available=True, skipped_reason="template needs cwd, none given")`,
   never a raw `TypeError`/crash (same shape as the existing `{prompt}`-missing guard,
   `evals.py:90-92`).
2. **Kill the whole process group on timeout, not just the direct child.** `subprocess.run`
   gets `start_new_session=True`; on `TimeoutExpired`, `os.killpg(os.getpgid(proc.pid),
   signal.SIGKILL)` before returning. A CLI's own spawned grandchildren (browser automation,
   an LSP server) no longer survive as undetected orphans inside a worktree.

**Dispatch flow:**

1. **Worktree pre-check, sequential creation.** Before creating anything, check for an
   existing `swarm/<agent>/<task-slug>` worktree or branch (`git worktree list` / `git branch
   --list`). If found: fail with "worktree for `<agent>` already exists from a prior run —
   remove it or pass `--resume`" — never propagate raw `git` stderr. Worktrees are created
   **sequentially** (avoids `.git` index/lock contention across 5 concurrent `git worktree
   add` calls); the 5 CLI dispatches themselves run **concurrently** once all worktrees exist.
2. **Per-worktree environment provisioning.** `uv sync` (matching this repo's `uv.lock`)
   inside each worktree before anything else — a fresh `git worktree add` doesn't carry
   `.venv/`, and skipping this would misattribute every agent as "failed" for an environment
   reason, not a code-quality one.
3. Invoke that agent's CLI via `run_adversary(spec, prompt, cwd=worktree_path, timeout=...)`
   from `CODE_FIX_RUNNERS` (not the research table) — the prompt is a code-editing task
   (description + acceptance criteria), not a research question.
4. After each run: `git -C <worktree> diff` captures what actually changed (empty diff is a
   real, recorded outcome — "ran, produced nothing" — not an error). `pytest -q` runs inside
   the provisioned worktree; a failure is recorded as **"tests failed"** only if the
   environment provisioned cleanly in step 2 — an environment failure is recorded distinctly
   as **"tests could not run"**, never conflated with the agent's own diff quality.
5. **Report persisted to disk, not stdout-only**: `.swarm-runs/<task-slug>/report.{md,json}`
   — per agent: status, diff, test result (failed/could-not-run/passed), elapsed time, bounded
   stdout/stderr tails (reusing `RunnerResult.as_dict()`'s existing truncation convention,
   `evals.py:72-76`). Also printed to stdout for the live run, but the file is the durable
   copy — closing the terminal must not lose it.
6. Real observed token/latency data (where the CLI's `--json`/`--format json` output exposes
   it — opencode, cline, muse's `--json` all do; kilo/kiro-cli fall back to wall-clock latency
   only, recorded honestly as `total_tokens=0` with a note, not estimated) gets recorded via
   `LedgerStore`/`Session.record_step()`, same schema `record_agent_session_v2.py` already
   establishes — `routed_model` = the agent name, `provider_name` = the CLI, `accepted`
   determined by whether the diff applies cleanly AND `pytest -q` passes against it.
7. **`oracle_best_model` / `counterfactual_latency_ms` are computed in a second pass, after
   all 5 agents finish** — not per-step during dispatch. These fields inherently require
   cross-agent comparison (which of the 5 actually passed tests, fastest/cheapest among those
   that did); in every existing usage (`record_agent_session_v2.py`) they were hand-picked by
   a human after the fact, and this spec doesn't change that contract, only automates the
   comparison step itself once all 5 outcomes are known. If zero agents pass, `oracle_best_model
   = "none"`, recorded honestly, not left null or guessed.
8. Nothing merges automatically. Reviewed by a human (via this session) before anything
   reaches `main`.

**Script interface** (follows the existing `record_agent_session_v2.py --db` argparse
convention):

```
dispatch_code_fix.py --target-repo <path> --task-slug <slug> --prompt-file <path>
                      [--agents opencode,cline,...]   # subset for a cheap first run;
                                                       # default: all of CODE_FIX_RUNNERS
                      [--resume]                      # reuse existing worktrees/branches
                      [--timeout SECONDS]              # per-agent, default 180
```

`--target-repo` is explicit and required (no implicit relative-path assumption, no
repeat of `record_agent_session_v2.py:41`'s `sys.path.insert(...parent.parent/"src")`
fragility) — for this spec's own first run, `--target-repo ../notelab` from [redacted-private-project].
`--agents` mirrors `spar()`'s existing `families` param (`_tools.py:179`) for a cheap
single-provider smoke run before committing to all 5.

**Cleanup**: `dispatch_code_fix.py --cleanup --task-slug <slug>` removes that run's 5
worktrees and branches (`git worktree remove --force` + `git branch -D`, looped) — not
automatic after a run (the worktrees ARE the reviewable artifact until a human has looked),
but a named, discoverable command instead of five manual git commands from memory.

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
- `run_adversary`'s two fixes get their own tests: (a) a template containing `{cwd}` with no
  `cwd=` passed returns a recorded `skipped_reason`, never raises; (b) a simulated child
  process that spawns a long-lived grandchild is confirmed NOT left running after
  `TimeoutExpired` (process-group kill).
- Component B: `dispatch_code_fix.py` itself gets a smoke test with all 5 `run_adversary`
  calls mocked (no real CLI invocation, no real cloud spend, in CI) — including the pre-check
  path (existing worktree/branch → actionable failure, not raw git stderr) and the env-failure
  path (missing `.venv` → "tests could not run", not "tests failed") — plus one real live run
  in this session (the gap #2 race) as the actual verification.
- The gap #2 fix itself: each agent's diff is accepted only if `pytest -q` passes inside its
  provisioned worktree with the diff applied — this is the objective bar, not agent self-report.

## Open questions

1. Per-agent timeout for the code-fix race — 180s (same as Component A) may be too short for
   a real multi-file code change with test-writing. Will adjust per observed behavior during
   the live run rather than guess up front.
2. If two or more agents produce equally-valid diffs, which gets recommended for merge is a
   judgment call made after seeing real results, not decided in advance.
3. `kilo run --dir <PATH>`'s help text reads "path on remote server if attaching," ambiguous
   for the local one-shot case used here — needs a live smoke test (not just `--help` text)
   before the first real run, to confirm it actually scopes `kilo` to the worktree rather than
   the current process cwd.

## Revision after eng + devex review (2026-09-26)

Both `/plan-eng-review` and `/plan-devex-review` ran against the original draft of this spec
(dispatched to background subagents, per the user's request to use them as sparring
partners). Verdict from both: do not implement as written. The eng review's findings were
delivered via its handback report but lost from this file to a concurrent write from the
devex review's own in-progress edit (both subagents ran in parallel against the same file) —
recorded here instead, alongside how every finding from both reviews was resolved above.

**Eng review findings (4, all addressed above):**
1. **[P1] Crash bug**: the original `family_runner` called `run_adversary(spec, p,
   timeout=180)` with no `cwd=`, but the draft's new `RunnerSpec`s required `{cwd}`
   substitution with no guard — `.replace("{cwd}", None)` raises `TypeError`. Fixed by
   removing `{cwd}` from Component A's runners entirely (they don't need it) and adding a
   proper guard to `run_adversary` for Component B's use of it.
2. **[P1] Isolation gap**: Component A used the same full-trust flags as Component B
   (`--yolo`, `--auto`, `-a`) with none of Component B's worktree isolation, diff capture, or
   review gate — and `spar()` is a far more casually-callable live MCP tool. Fixed by removing
   the trust flags from Component A entirely rather than adding matching isolation: research
   never needed write access, so there's no capability left to isolate against.
3. **[P2]** `kilo --dir`'s ambiguous help text — added as Open question #3, needs a live
   smoke test before the first real run.
4. **[P2]** Worktree-creation concurrency was unspecified — now explicit: sequential creation,
   concurrent dispatch (Component B, step 1).

**DevEx review findings (7, all addressed above)** — full review, persona, journey map,
scorecard (4/10 pre-fix), and the originating T1-T7 task list are preserved below as the
review record. Each finding's fix:
1. No worktree lifecycle/cleanup story → pre-check + `--resume` + `--cleanup` (Component B).
2. Timeout didn't kill grandchild processes → `run_adversary` process-group kill (above).
3. Report format/persistence unspecified → `.swarm-runs/<task-slug>/report.{md,json}` (Component B, step 5).
4. No per-worktree env provisioning → `uv sync` before `pytest -q`, failure modes distinguished (Component B, step 2 & 4).
5. Cross-repo path coupling, fragile `sys.path` precedent → explicit `--target-repo` arg (Component B, script interface).
6. Ledger field (`oracle_best_model`) computation unaddressed → explicit second-pass, cross-agent comparison (Component B, step 7).
7. 6th-provider onboarding regression (docstring "~1 line" claim) → no longer broken: Component A's `KNOWN_RUNNERS` stays `{cwd}`-free, Component B's `CODE_FIX_RUNNERS` is a separate table that was never bound by that promise.

Both reviews' full reports (scorecards, journey maps, task lists) are preserved below as the
record of what was reviewed and why the design changed — not because the tasks they list
(T1-T7) are separately open; they're folded into the design above.

## DX Review (plan-devex-review)

*Non-interactive review pass — spawned/background session, no live human available to
answer AskUserQuestion. Persona, mode and target were auto-decided (recommended option)
rather than confirmed interactively; flagged below wherever that matters.*

### Developer Persona Card
```
TARGET DEVELOPER PERSONA
========================
Who:       The repo's own solo maintainer (also the ledger's only reader).
Context:   Runs `dispatch_code_fix.py` and `spar()` locally, alone, possibly late at
           night, with no teammate to ask and no support channel.
Tolerance: High for terminal/CLI friction (this person built a stdlib-only, zero-dep
           MCP server by choice). Near-zero for silent data loss — orphaned worktrees,
           leaked background processes, or a report that vanishes when the terminal
           closes — because there's no one else to notice or fix it.
Expects:   Every failure mode identifies which of the 5 agents failed, why, and what
           state (worktree/branch/process) was left behind, without re-deriving it by
           hand.
```
This is internal ops tooling for a single operator, not a distributed dev product —
Passes 4 (Docs/Findability), 5 (Upgrade/Migration), 7 (Community/Ecosystem) are scored
light/N/A below rather than skipped (anti-skip rule), since a public-adoption funnel
genuinely doesn't apply here.

### Developer Empathy Narrative
It's 1am. I run `dispatch_code_fix.py`. It prints five lines as worktrees get created,
then goes quiet for 180s. Nothing streams — I don't know if kilo-cli is thinking or
hung. At T+180s, four agents report done, one didn't. Is it still running (`ps` shows
what)? Did `subprocess.run`'s timeout actually kill it, or is there an orphaned `kilo`
process still writing files into a worktree I'm about to inspect? I want a diff for
each agent — where did it go? Printed to stdout that already scrolled past, or is it
on disk? I re-run to retry the stuck one — does `git worktree add` on the same branch
name explode because the last run's worktree is still there? I go to clean up — five
`git worktree remove` and `git branch -D` commands, by hand, from memory of the naming
scheme. None of this is hypothetical: it's exactly what the spec leaves unspecified.

### Competitive DX Benchmark
Note: TTHW / competitive-tier framing doesn't map to internal, single-operator tooling
(no external research was run for this reason — see mode note below). The real
baseline is the repo's own existing precedent: `[redacted-private-project]/examples/record_agent_session_v2.py`,
which hand-authors every ledger field after the fact, with human-written comments like
`# ESTIMATED (short prompt/response, plausible range)` next to real numbers. That
script is a retrospective narrative tool. This spec asks a *live automated* dispatcher
to populate the same schema in real time — a materially different job the schema
wasn't built for (see Pass 2 and Pass 3 below).

### Magical Moment Specification
**Moment:** five real CLI agents race the identical task; the operator gets back diffs
+ pass/fail in one pass and can eyeball which (if any) is mergeable.
**Delivery vehicle (as specified):** `dispatch_code_fix.py`'s stdout, plus ledger rows.
**Gap:** the spec names this moment (Component B step 5, line 106-108) but never
specifies the report's *shape* — no schema, no example, no statement of whether it's
saved to a file. If it's stdout-only, the magical moment survives exactly as long as
the terminal scrollback. Fix: write a per-run JSON/markdown report to a fixed path
(e.g. `.swarm-runs/<task-slug>/report.md`) in addition to printing it.

### Developer Journey Map
```
STAGE               | DEVELOPER DOES                          | FRICTION POINTS                                    | STATUS
--------------------|------------------------------------------|-----------------------------------------------------|--------
1. Run (Component A)| `spar()` via MCP tool call                | None new — reuses existing detect_runners() UX      | ok
2. Install (6th     | Add RunnerSpec to KNOWN_RUNNERS           | Spec adds a 2nd placeholder ({cwd}) some runners     | deferred
   provider)         | (evals.py docstring claims "~1 line")     | need and some don't — the "1 line" claim goes stale  |
                     |                                           | with no updated onboarding checklist for verifying   |
                     |                                           | a new CLI's non-interactive/yolo flag and cwd support|
3. Run (Component B)| `python examples/dispatch_code_fix.py`    | Lives in [redacted-private-project], targets notelab worktrees — no    | deferred
                     | from [redacted-private-project], pointed at notelab        | stated arg/config for the target-repo path (fragile, |
                     |                                           | same sys.path-hack pattern already visible in v2.py) |
4. Wait              | 180s(+) per agent, concurrently           | No streaming/progress signal; can't tell "thinking"  | deferred
                     |                                           | from "hung" until the subprocess timeout fires        |
5. Debug a stuck run | Inspect process / worktree state          | subprocess.run(timeout=) kills only the immediate     | deferred
                     |                                           | child — a CLI's own spawned grandchildren can survive |
                     |                                           | as orphans in the worktree; nothing in the spec kills |
                     |                                           | or even detects them                                  |
6. Read results      | Look at diffs + pytest pass/fail per agent| Report shape/location unspecified (see Magical Moment)| deferred
7. Re-run / retry    | Fix timeout, re-run one agent             | Worktree/branch naming is `swarm/<agent>/<task-slug>` | deferred
                     |                                           | with no idempotency — `git worktree add` on an        |
                     |                                           | existing branch fails; no stated recovery              |
8. Clean up          | Remove 5 worktrees + 5 branches           | Not mentioned anywhere in the spec                     | deferred
```

### First-Time Developer Confusion Report
```
FIRST-TIME DEVELOPER REPORT
============================
Persona: solo maintainer, first live run of Component B
Attempting: race gap #2 fix across all 5 agents

T+0:00  Runs dispatch_code_fix.py from [redacted-private-project]. Five "creating worktree" lines print.
        No indication of where these worktrees live relative to notelab's checkout.
T+0:05  Five agent processes launch. No per-agent progress output — script goes silent.
T+3:00  Timeout window (180s) closes for the fastest agents. Some report; kilo-cli
        doesn't. Unclear if it's still running, timed out, or crashed silently.
T+3:05  Checks `ps aux | grep kilo` manually — the spec gave no built-in way to do this.
        Finds a live kilo process still writing files in its worktree, well past 180s.
T+5:00  Script exits. Prints... something (unspecified format). No file on disk to
        revisit later without re-running.
T+5:30  Wants to retry kilo alone. Runs the script again — `git worktree add` fails,
        branch `swarm/kilo/gap2-semantic-fix` already exists from the aborted run.
T+8:00  Manually runs `git worktree remove --force` + `git branch -D` five times to
        reset state before the next attempt. Gave up trying to keep the failed run's
        diffs for later comparison — they were only in scrollback.
```
Every friction point above traces to gaps the spec itself leaves open (Section: "First
real run", lines 110-133, and Component B design, lines 85-108) — none is invented.

### "NOT in scope" section
- Public-facing docs/onboarding funnel for this tooling — genuinely N/A (internal,
  single-operator use); not deferred, just doesn't apply.
- Streaming/live progress output from the 5 CLIs mid-run — real UX win, but a bigger
  build than this spec's stated scope; flagged as a TODO below, not blocking.
- A dashboard reading the ledger live — the spec already explicitly excludes this
  (Non-goals, line 34-36); DX review agrees this is correctly out of scope.

### "What already exists" section
- `run_adversary`'s never-raises contract (evals.py:79-100) and the `skipped_reason`
  pattern are a genuinely good existing error-legibility precedent — Component B should
  reuse this shape (status + reason string) for its own per-agent report rather than
  inventing a new one.
- `record_agent_session_v2.py`'s inline provenance comments (`# REAL` vs `# ESTIMATED`)
  are a good existing pattern for honesty about data quality — Component B's automated
  writes should carry the same distinction (e.g. `total_tokens=0` already does this per
  the spec, line 100-101 — good, keep it).
- `RunnerResult.as_dict()`'s `stdout_tail`/`stderr_tail` truncation (evals.py:72-76) is
  the existing template for bounded diagnostic output; the per-agent report in
  Component B should follow the same bounded-tail convention, not dump raw CLI output
  unbounded into the ledger or a report file.

### Pass 1: Getting Started Experience — 6/10
Running Component A (`spar()`) has zero new friction — it's an existing MCP tool call,
no new install step. Running Component B for the first time is where this scores lower:
the script isn't packaged as a single command (`cwd`-dependent, cross-repo, per the
Journey Map stage 3), and there's no stated "first run" smoke path shorter than the
full 5-agent live race — the spec's own Testing section (line 141-143) says the smoke
test mocks all 5 `run_adversary` calls, which is good, but the SPEC doesn't say the
human-facing script has a `--dry-run` or `--agents opencode` single-agent flag for a
cheap first try. Fix to 8: add a `--agents` filter (mirrors `spar()`'s existing
`families` param, `_tools.py:179`) so a first run can be one agent, not five.

### Pass 2: API/CLI/SDK Design — 5/10
`family_runner`'s design (Component A) is consistent with the existing `spar()`
signature and reuses `run_adversary` — good persona fit, matches how this codebase
already thinks about runners. Component B's `dispatch_code_fix.py` interface is
undefined beyond "a script" — no stated CLI args (target repo, task prompt source,
timeout override, `--agents` subset, output path). Given the existing codebase already
has an argparse convention (`record_agent_session_v2.py`'s `--db` flag), Component B
should follow the same pattern. Bigger gap: the ledger `Attribution` fields
(`oracle_best_model`, `counterfactual_latency_ms`, `force_regret`) are, in every
existing usage, hand-picked by a human after the fact — the spec claims Component B
writes "same schema" but never says what an automated one-shot dispatcher computes for
these. `oracle_best_model` specifically needs cross-agent comparison, so it cannot be
written per-step during the race — only after all 5 finish, in a second pass. Not
addressed; this is a real modeling gap, not just wiring.

### Pass 3: Error Messages & Debugging — 4/10
Three real error paths traced:
1. **A CLI agent hangs past its timeout.** Current: `run_adversary` catches
   `TimeoutExpired` and returns `timed_out=True` (evals.py:97-98) — good, this part is
   fine. Gap: `subprocess.run(timeout=...)` kills only the direct child; any
   grandchild process the CLI spawned (browser automation, an LSP server, a
   sub-agent) is not guaranteed to die and isn't checked for. What the operator sees:
   nothing — no orphan-detection, no warning. Fix: after a timeout, at minimum log the
   child's PGID and note in the report that grandchildren may still be running;
   ideally spawn with `start_new_session=True` and kill the whole process group.
2. **`git worktree add` fails because the branch already exists (re-run).** Current:
   unhandled — the spec doesn't mention this path at all. What the operator would see:
   a raw `git` CLI error with no context tying it to "this is a re-run of a prior
   attempt." Fix: check `git worktree list`/`git branch --list` before creating, and
   surface "worktree for <agent> already exists from a prior run — remove it or use
   --resume" rather than propagating git's raw stderr.
3. **`pytest -q` fails inside a worktree for environment reasons, not the agent's
   diff** (e.g. the worktree has no `.venv`, since this repo uses `uv` + `uv.lock` at
   the top level and a fresh `git worktree add` does not carry `.venv/`). Current:
   unaddressed. What the operator would see: every agent recorded as "failed" when the
   real cause is a missing environment, misattributing the outcome. Fix: `uv sync`
   (or equivalent) per worktree before running tests, and distinguish
   "tests failed" from "tests couldn't run" in the report.
None of these three rise to Tier 1 (Elm-style problem+cause+fix) — all three currently
either propagate a raw subprocess/git error or fail silently.

### Pass 4: Documentation & Learning — N/A (5/10 baseline, not scored down)
Internal single-operator tool — no external docs funnel needed. The spec itself is the
documentation. One real gap: the spec doesn't say whether `dispatch_code_fix.py --help`
exists or what a first-time (future) reader needs to know beyond this design doc.

### Pass 5: Upgrade & Migration Path — N/A (light)
Not applicable in the SDK-versioning sense. The relevant analog — "does adding a 6th
provider break anything for the existing 5" — is covered under Pass 6 (onboarding a
new provider) rather than here.

### Pass 6: Developer Environment & Tooling — 4/10
This is the pass that matters most for this spec, per the review's own brief.
- **CI/CD:** Not addressed — Component B is explicitly a live, human-supervised run
  (correctly, per Non-goals). Component A's tests are mockable and CI-safe (good, spec
  says so explicitly, line 137-143).
- **Local env reproducibility:** Real gap — `uv`/`.venv` provisioning per worktree is
  unaddressed (see Pass 3, error path 3). Five parallel `git worktree add` calls
  against the same `.git` also risk lock contention if issued concurrently rather than
  sequentially before the 5 CLI dispatches — the spec doesn't say which.
- **Observability/dry-run:** No `--dry-run`, no streaming output, no `--agents` subset
  flag (see Pass 1). At minimum, per-agent start/elapsed-time logging as each finishes
  (not just a final report) would let an operator tell "still running" from "hung"
  without a manual `ps` check.
- **Onboarding a 6th provider:** Real, concrete gap. `evals.py`'s own docstring
  (line 12-13) currently promises "adding another is ~1 line in KNOWN_RUNNERS." This
  spec breaks that promise silently: it adds a second placeholder (`{cwd}`) that only
  3 of the 9 total runners need, and the verification step ("checked live via --help
  this session") is manual and undocumented as a repeatable procedure. Fix: update the
  docstring to reflect the new `{cwd}` convention, and add a one-line smoke-test
  command a maintainer can run against a new CLI to confirm non-interactive + cwd
  support before adding it to `KNOWN_RUNNERS` — turning "trust the session notes" into
  a repeatable check.

### Pass 7: Community & Ecosystem — N/A
Single-operator internal tool; no community surface. Not scored down.

### Pass 8: DX Measurement & Feedback Loops — 3/10
No instrumentation proposed for this spec's own reliability: no count of how often a
re-run is needed, no tracking of orphaned-process incidents, no record of which
provider's flags needed correction after the fact. Given this is explicitly framed as
"the first real proof this works end-to-end" (Component B intro), the absence of any
plan to capture what went wrong operationally during that first run (beyond the
per-task ledger entries) means lessons from the first live race won't be captured
anywhere durable. Fix: at minimum, log worktree-lifecycle and timeout/orphan incidents
to the same ledger or a sibling log, not just task outcomes.

### DX Scorecard
```
+====================================================================+
|              DX PLAN REVIEW — SCORECARD                             |
+====================================================================+
| Dimension            | Score  | Prior  | Trend  |
|----------------------|--------|--------|--------|
| Getting Started      | 6/10   | —      | —      |
| API/CLI/SDK          | 5/10   | —      | —      |
| Error Messages       | 4/10   | —      | —      |
| Documentation        | N/A    | —      | —      |
| Upgrade Path         | N/A    | —      | —      |
| Dev Environment      | 4/10   | —      | —      |
| Community            | N/A    | —      | —      |
| DX Measurement       | 3/10   | —      | —      |
| Overall DX           | 4/10   | —      | —      |
+--------------------------------------------------------------------+
| TTHW                 | N/A — internal single-operator tooling       |
| Competitive Rank     | N/A                                          |
| Magical Moment       | designed (race + diff/test report), but       |
|                       | report format/persistence unspecified        |
| Product Type         | Internal dev-ops CLI script (multi-provider   |
|                       | dispatch), plus an MCP tool fix               |
| Mode                 | DX POLISH (auto-decided — internal ops tool,  |
|                       | enhancement to existing capability, no live   |
|                       | user to confirm interactively)                |
+====================================================================+
| DX PRINCIPLE COVERAGE                                               |
| Zero Friction      | gap — no --dry-run / single-agent first try     |
| Learn by Doing     | covered — smoke test with mocked runners exists |
| Fight Uncertainty  | gap — error paths 1-3 above (Pass 3)            |
| Opinionated + Escape Hatches | gap — no --agents filter, no --resume |
| Code in Context    | covered — reuses run_adversary end to end       |
| Magical Moments    | gap — report shape/persistence unspecified      |
+====================================================================+
```

### DX Implementation Checklist
```
DX IMPLEMENTATION CHECKLIST
============================
[ ] --agents / single-provider filter for cheap first runs (mirrors spar()'s families)
[ ] Worktree pre-check: detect existing swarm/<agent>/<task-slug> before creating,
    fail with an actionable message instead of raw git stderr
[ ] Kill the whole process group on timeout, not just the direct child (evals.py)
[ ] Per-agent report persisted to disk (not stdout-only), bounded stdout/stderr tails
    reusing RunnerResult.as_dict()'s existing truncation convention
[ ] Per-worktree environment provisioning (uv sync) before pytest -q, and distinguish
    "tests failed" from "tests could not run"
[ ] Explicit --target-repo / config for the cross-repo ([redacted-private-project] -> notelab) path,
    not an implicit relative-path assumption
[ ] Update evals.py's "~1 line in KNOWN_RUNNERS" docstring claim + add a repeatable
    smoke-test command for verifying a new provider's cwd/non-interactive support
[ ] Explicit worktree cleanup step/flag (script does it, or documents the exact
    5x git worktree remove + branch -D commands)
[ ] Automated computation (or explicit "TODO: fill by hand") for oracle_best_model /
    counterfactual_latency_ms — currently these are only ever human-authored
```

## Implementation Tasks
Synthesized from this review's findings. Each task derives from a specific finding
above. Run with Claude Code or Codex; checkbox as you ship.

- [ ] **T1 (P1, human: ~2h / CC: ~20min)** — dispatch_code_fix.py — Persist a per-agent
  report to disk (status/diff/test-result/timing) instead of stdout-only
  - Surfaced by: Magical Moment Specification, Pass 3 error path 3, Confusion Report T+5:00
  - Files: [redacted-private-project]/examples/dispatch_code_fix.py (new)
  - Verify: run smoke test with mocked run_adversary calls, inspect written report file
- [ ] **T2 (P1, human: ~2h / CC: ~20min)** — dispatch_code_fix.py — Detect an existing
  swarm/<agent>/<task-slug> worktree/branch before creating a new one; fail with an
  actionable message, not raw git stderr
  - Surfaced by: Journey Map stage 7, Confusion Report T+5:30, Pass 3 error path 2
  - Files: [redacted-private-project]/examples/dispatch_code_fix.py (new)
  - Verify: run twice in a row against the same task-slug; second run must not crash
- [ ] **T3 (P1, human: ~1h / CC: ~10min)** — evals.py — On subprocess timeout, kill the
  full process group (start_new_session=True + os.killpg) rather than only the direct
  child, and note in RunnerResult when grandchildren may have survived
  - Surfaced by: Pass 3 error path 1, empathy narrative
  - Files: src/notelab/research/evals.py (run_adversary, lines 79-100)
  - Verify: new test simulating a child that spawns a long-lived grandchild; assert it
    is not left running after TimeoutExpired
- [ ] **T4 (P2, human: ~1h / CC: ~10min)** — dispatch_code_fix.py — Provision each
  worktree's environment (uv sync / equivalent) before running pytest -q, and
  distinguish "tests failed" from "tests could not run"
  - Surfaced by: Pass 3 error path 3, Pass 6
  - Files: [redacted-private-project]/examples/dispatch_code_fix.py (new)
  - Verify: run in a freshly created worktree with no .venv; confirm it doesn't
    misattribute an environment failure to the agent's diff
- [ ] **T5 (P2, human: ~30min / CC: ~5min)** — evals.py — Update the KNOWN_RUNNERS
  docstring's "~1 line" claim to reflect the new {cwd} placeholder, and document a
  repeatable smoke-test command for verifying a new CLI's non-interactive/cwd support
  - Surfaced by: Pass 6 (onboarding a 6th provider)
  - Files: src/notelab/research/evals.py (lines 1-19, 41-49)
  - Verify: manual read-through; docstring claim matches actual KNOWN_RUNNERS shape
- [ ] **T6 (P3, human: ~1h / CC: ~15min)** — dispatch_code_fix.py — Add --agents
  filter for a cheap single-provider first run, mirroring spar()'s families param
  - Surfaced by: Pass 1, DX Principle Coverage (Zero Friction)
  - Files: [redacted-private-project]/examples/dispatch_code_fix.py (new), src/notelab/_tools.py:179 (reference pattern)
  - Verify: `dispatch_code_fix.py --agents opencode` runs exactly one agent
- [ ] **T7 (P3, human: ~2h / CC: ~20min)** — [redacted-private-project] ledger — Decide and document how
  Component B computes oracle_best_model / counterfactual_latency_ms automatically
  (likely a second pass after all 5 agents finish, not per-step)
  - Surfaced by: Pass 2
  - Files: [redacted-private-project]/src/redacted_private_project/routing_monitor/ledger/schema.py, dispatch_code_fix.py (new)
  - Verify: a real 5-agent run produces a ledger row with oracle_best_model set from
    actual cross-agent comparison, not a hardcoded placeholder

_No new tasks from Pass 4, Pass 5, Pass 7 (N/A for this internal single-operator tool)._

### Unresolved Decisions
This was a non-interactive review pass (spawned/background session). The following
would normally be interactive AskUserQuestion decisions; they were auto-decided using
the recommended option instead, and should be confirmed by the plan's author before
implementation:
- Mode: DX POLISH was auto-selected (enhancement to existing capability, internal ops
  tool — no ambitious scope expansion proposed).
- Persona: "solo maintainer, no live support," auto-derived from the spec's own framing
  ("a human reviews before anything reaches main," "I present the results").
- Outside-voice (Codex) second opinion: attempted, unavailable — Codex CLI hit its
  usage limit (retry after 2026-10-23 per the CLI's own message). No cross-model
  coverage on this review.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | not run |
| Outside Review | `codex exec` (this run) | Independent 2nd opinion | 1 | unavailable | Codex usage limit hit; no cross-model coverage |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_resolved | 4 findings (2×P1 crash bug + isolation gap, 2×P2); report lost to concurrent write with DX review, preserved in "Revision after eng + devex review" section |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | not applicable (no UI) |
| DX Review | `/plan-devex-review` | Developer experience gaps | 1 | issues_open | score: N/A → 4/10, TTHW: N/A → N/A (internal ops tool, no adoption funnel) |

- **OUTSIDE COVERAGE:** provider=codex, phase=plan-review, completion state=unavailable (usage-limit error, no findings returned).
- **VERDICT:** DX REVIEW COMPLETE, ISSUES RESOLVED — the operability gaps found (worktree lifecycle, timeout/orphan-process handling, report persistence, cross-repo path coupling, ledger-field automation) are addressed in the design above; see "Revision after eng + devex review." Eng review also complete, also resolved (same section).

**UNRESOLVED DECISIONS:**
+ Mode/persona/target were auto-decided (recommended option), not interactively confirmed — see "Unresolved Decisions" above; confirm before implementation.
+ Outside-voice (Codex) coverage unavailable this run (usage limit) — no cross-model check on these findings.
+ T1-T7 are folded into the revised design above, not separately open — see "Revision after eng + devex review."
