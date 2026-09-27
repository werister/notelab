# Swarm loop orchestration: critique/refine chains, cross-vote councils, real rosters

## Problem

The swarm dispatcher (`[redacted-private-project]/examples/dispatch_code_fix.py`) built and proven this
session (`docs/superpowers/specs/2026-09-26-multi-provider-subagent-swarm-design.md`) does
exactly one thing: race every available agent on the identical task in parallel, gate
acceptance on tests, let a human pick a winner. That's real, working, and already shipped
one genuine fix into `main`. It has three real gaps, all surfaced by this session's own live
runs, not speculative:

1. **One model per provider.** `CODE_FIX_RUNNERS` pins exactly one model each for
   opencode/muse/cline/kilo/kiro-cli. Live testing this session found real rosters:
   4 confirmed-working free kilo models, 7 confirmed-working cline models (2 genuinely free,
   5 ClinePass — already-paid, so also effectively free), several opencode free models with
   materially different reliability (`nemotron-3.5-lightning-free` hallucinated a garbled
   shell command under real load; `nemotron-3-ultra-free` showed better judgment, correctly
   recognizing already-completed work rather than duplicating it). One pinned model per
   provider throws this variety away.
2. **No iteration — only race-then-pick.** Every agent works in isolation; nothing lets a
   cheap model critique or refine another's output, or lets a panel of cheap models reach
   consensus, before a human (or a frontier model) has to look. The user's original framing:
   *"claude loops to a free tier, another free tier checks on the free tier, refines it,
   stress tests it, and only then comes back to the frontier"* — the current dispatcher
   cannot do this at all.
3. **No circuit breakers for the real failure modes already observed.** This session hit,
   live: a hard-exhausted pay-per-token balance (cline Credits, $0.01), a hard monthly quota
   cap (kiro-cli, account-wide, unrelated to which model is requested), a broken CLI flag
   (kiro-cli's `--model` errors "Method not found" in the installed version), and a model
   hallucinating garbled shell syntax under load (opencode). None of these are handled --
   `dispatch_one` just lets each fail as a generic `error`/`timed_out`, indistinguishable
   from "the model tried and produced nothing."

Sakana AI's Fugu (see `docs/RESEARCH-2026-09-27-sakana-fugu-learned-orchestration.md`) is
the unconstrained version of the same idea — a *trained* 7B conductor model deciding
routing/roles/verification/synthesis over a pool of frontier workers, with no cost ceiling.
This spec is the cost-constrained, hand-designed version: same shape of decision (who works
on this, does it need another pass, when do we stop and escalate), made with heuristics and
real observed data instead of a trained policy, because free-tier CLI credits are a hard
constraint the Fugu paper never had to solve for.

## Non-goals

- **Not building Pattern B (parallel race + cross-vote council) in this plan.** Both patterns
  are designed below — the user explicitly wants both eventually — but Pattern A (the
  critique/refine chain) plus the shared infrastructure (rosters, circuit breakers, per-hop
  ledger recording) is Phase 1. Pattern B is Phase 2, deliberately deferred until Phase 1's
  real usage data exists to design the voting/consensus threshold against real numbers
  instead of a guess.
- **Not a trained/learned router.** Model selection within a roster, and the
  escalate-to-Claude decision, are both rule-based heuristics grounded in this session's real
  data (rotation-prone free tiers, per-provider reliability signals) — not a Fugu-style
  trained policy. That's out of scope; flagged as a real future direction in the Fugu
  research doc, not attempted here.
- **Not touching Component A (`spar()`, notelab's research-only dispatch)** — this is
  entirely Component B ([redacted-private-project]'s code-fix dispatcher) territory.
- **Not removing or replacing the existing single-shot race mode.** `--agents` (a subset) and
  the plain race (no chain, no council) stay available — the chain is a new mode
  (`--mode chain`, default stays the current race behavior), not a forced replacement.

## Design — shared infrastructure (Phase 1)

### 1. Real, multi-model rosters per provider

Replace each provider's single `RunnerSpec` with a list, in `_load_code_fix_runners()`:

```python
CODE_FIX_ROSTERS: dict[str, list[RunnerSpec]] = {
    "opencode": [
        RunnerSpec("opencode", "opencode", ["run", "--dir", "{cwd}",
                   "-m", "opencode/nemotron-3-ultra-free", "{prompt}"]),
        RunnerSpec("opencode", "opencode", ["run", "--dir", "{cwd}",
                   "-m", "opencode/mimo-v2.6-flash-free", "{prompt}"]),
    ],
    "muse": [
        RunnerSpec("muse", "muse", ["exec", "--workspace", "{cwd}", "--yolo", "{prompt}"]),
    ],
    "cline": [
        RunnerSpec("cline", "cline", ["--cwd", "{cwd}",
                   "-m", "cline-free/deepseek-v4.1-flash", "{prompt}"]),
        RunnerSpec("cline", "cline", ["--cwd", "{cwd}",
                   "-m", "cline-free/muse-spark-1.3-contributor", "{prompt}"]),
        RunnerSpec("cline", "cline", ["--cwd", "{cwd}",
                   "-m", "cline-pass/glm-5.3", "{prompt}"]),
        RunnerSpec("cline", "cline", ["--cwd", "{cwd}",
                   "-m", "cline-pass/kimi-k3", "{prompt}"]),
        RunnerSpec("cline", "cline", ["--cwd", "{cwd}",
                   "-m", "cline-pass/deepseek-v4-pro", "{prompt}"]),
        RunnerSpec("cline", "cline", ["--cwd", "{cwd}",
                   "-m", "cline-pass/minimax-m3", "{prompt}"]),
        RunnerSpec("cline", "cline", ["--cwd", "{cwd}",
                   "-m", "cline-pass/qwen3.8-max", "{prompt}"]),
    ],
    "kilo": [
        RunnerSpec("kilo", "kilo", ["run", "--dir", "{cwd}", "--auto",
                   "-m", "kilo/kilo-auto/free", "{prompt}"]),
        RunnerSpec("kilo", "kilo", ["run", "--dir", "{cwd}", "--auto",
                   "-m", "kilo/nvidia/nemotron-3.5-lightning:free", "{prompt}"]),
        RunnerSpec("kilo", "kilo", ["run", "--dir", "{cwd}", "--auto",
                   "-m", "kilo/nvidia/nemotron-3-ultra-550b-a55b:free", "{prompt}"]),
        RunnerSpec("kilo", "kilo", ["run", "--dir", "{cwd}", "--auto",
                   "-m", "kilo/stepfun/step-3.7-flash:free", "{prompt}"]),
    ],
    "kiro-cli": [
        RunnerSpec("kiro-cli", "kiro-cli", ["chat", "{prompt}", "--no-interactive", "-a"]),
    ],
}
```

Every model id in this table was invoked live this session and returned real output (not
guessed from a catalog page — `qwen/qwen3.8-27b:free` on kilo and `z-ai/glm-5.3-flash` on
cline were both tried and dropped: the first returned nothing in 40s, the second silently
routed through the empty Cline Credits balance instead of the free namespace it looked like
from search results alone). `kiro-cli`'s `--model` flag itself errors ("Method not found")
in the installed CLI version, so it stays single-model until that's fixed upstream — recorded
as a known limitation, not silently worked around.

A **"lane"** is now `(provider, model_index)`, not just `provider` — `dispatch_one`'s
`spec.name` (still just `"opencode"`, `"cline"`, etc.) is no longer a unique identifier
against a roster; add a `lane_id` (e.g. `"cline#2"`) threaded through `AgentOutcome` and the
ledger so per-model (not just per-provider) performance is queryable.

### 2. Circuit breakers for observed failure signatures

New `classify_failure(stderr: str, stdout: str) -> str | None` in `dispatch_code_fix.py`,
returning one of `"quota_exhausted"`, `"broken_flag"`, `"garbled_output"`, or `None` (no known
pattern matched — falls through to the existing generic error/timeout handling unchanged):

```python
_QUOTA_PATTERNS = [
    "insufficient balance", "monthly usage limit", "add credits to continue",
]
_BROKEN_FLAG_PATTERNS = ["method not found"]

def classify_failure(stderr: str, stdout: str) -> str | None:
    low = (stderr + stdout).lower()
    if any(p in low for p in _QUOTA_PATTERNS):
        return "quota_exhausted"
    if any(p in low for p in _BROKEN_FLAG_PATTERNS):
        return "broken_flag"
    if _looks_garbled(stdout):
        return "garbled_output"
    return None
```

`_looks_garbled()`: a bounded heuristic, not a model call (keeping detection free and fast) —
flags output as garbled if it contains a shell-syntax-error marker (`"parse error near"`,
`"unexpected token"`) immediately following a tool-call block, OR if a single line mixes
non-ASCII characters with ASCII shell-metacharacters (`|`, `)`, `...`) at a density above a
fixed threshold (the actual opencode failure: `"pip3 install |)ك... ... ... Diskussion..."`).
False positives are acceptable (worst case: a working-but-weird output gets retried once) —
false negatives (missing real garbling) are the failure mode that actually hurt this session.

**Effect of each classification** in the dispatch loop:
- `quota_exhausted`: mark that (provider, model) pair `unavailable_this_run` — skip it for
  every remaining task in the same invocation (don't burn a second attempt against a hard
  account-level cap that a retry cannot fix), and skip straight to the next roster entry for
  that provider if one exists.
- `broken_flag`: log once, fall back to that provider's roster entry with no `-m` override
  (the tool's own default), don't retry the same broken invocation.
- `garbled_output`: retry ONCE against the next roster entry for that same provider (not the
  same model — the point is escaping whatever triggered the garbling, not hoping for luck on
  a retry of the identical call).

### 3. Pattern A — implement → critique → refine chain

New `dispatch_chain()` function, parallel to today's `main()`'s race path, selected via
`--mode chain` (default `--mode race`, today's existing behavior, unchanged):

1. **Implement**: one lane from the target roster (rotate deterministically — round-robin by
   task-slug hash, not random, so a re-run with `--resume` picks up the same lane) implements
   the task in its own worktree, exactly like today's `dispatch_one`.
2. **Critique**: a lane from a **different provider** (cross-family, the same "Law 2"
   principle `consensus_gate.sh` already establishes) reviews the diff against the original
   prompt + acceptance criteria, using a critique prompt template (below) — this is a plain
   `run_adversary` call too, no new mechanism, just a different prompt and no worktree write
   access needed (read-only: point it at the SAME worktree the implementer used, but its
   `RunnerSpec` for this role never gets `--yolo`/`--auto`/`-a`, matching Component A's
   research-only design principle — a critique never needs write access).
3. **Refine**: the ORIGINAL implementer lane gets the critique's output appended to a new
   prompt and applies fixes, in the same worktree (so the diff accumulates, not overwrites).
4. Repeat 2→3 up to `--max-rounds` (default 2). Stop early if a critique round returns "no
   issues found" (a simple, explicit sentinel the critique prompt template asks for).
5. Gate acceptance exactly like today: `capture_diff` + `provision_env` + `run_tests`.
6. **Escalate to Claude** (write a clearly-marked "needs frontier review" report entry, no
   Claude API call made automatically — this repo's principle throughout, nothing auto-merges
   and nothing auto-escalates real spend without a human in the loop) when: max rounds
   exhausted without a clean critique, OR every lane in the implement role's provider is
   `unavailable_this_run` (circuit-breaker exhaustion), OR the final diff still fails tests
   after the last refine round.

**Critique prompt template** (new file, `examples/prompts/_critique_template.txt`, with
`{task_prompt}` and `{diff}` substitution points) — deliberately mirrors the spec-compliance
+ code-quality review prompts used manually, live, throughout this session (both worked well
in practice): ask explicitly for missing requirements, scope creep, and correctness bugs,
require file:line references, and require an explicit `NO ISSUES FOUND` sentinel line when
clean (the loop's stopping condition parses for this exact string, not sentiment).

### 4. Per-hop ledger recording (the actual dashboard payoff)

Extend `record_ledger()`'s per-outcome loop: a chain run creates ONE `AgentOutcome`-like
record per HOP, not one per lane. New `HopOutcome` dataclass (implement/critique/refine
share the same shape as today's `AgentOutcome` plus `role: str` and `round_num: int`), and
`tags` gains `f"role:{role}"` and `f"round:{round_num}"` alongside the existing
`swarm_race`/task-slug tags. `decision_reason` names which lane critiqued which (e.g.
`"cline#3 critiqued kilo#1's round-1 diff"`) — this is the literal answer to "show which
loops between agents are working well, under which circumstances": a dashboard reading this
ledger can now render the actual implement→critique→refine PATH a task took, not just a
final pass/fail per provider.

## Design — Pattern B, deferred to Phase 2 (documented now, not built)

Parallel race (today's existing mode) + a SEPARATE voting panel: after the race completes,
2-3 lanes NOT among the racers (different providers where possible) each independently rank
the racers' diffs (not told which lane produced which, to reduce brand/style bias) via a
ranking prompt template (same shape as the critique template, but scoring instead of
pass/fail). Consensus above a threshold (exact number TBD in the Phase 2 spec, once Phase 1's
real critique-quality data exists to calibrate against) picks a merge-ready winner without a
human; a split vote or no consensus escalates. Explicitly NOT built in this plan — the
rationale for deferring is in Non-goals above.

## Testing

- `classify_failure`/`_looks_garbled`: unit tests against the EXACT real stderr/stdout
  strings this session captured live (cline's `"Insufficient balance..."`, kiro-cli's
  `"monthly usage limit"`, kiro-cli's `"Method not found"`, opencode's actual garbled
  `pip3 install |)ك...` string) — regression tests against real, not synthetic, failure text.
- `dispatch_chain`: mocked `run_adversary` per hop (implement returns a diff, critique returns
  either `NO ISSUES FOUND` or a finding, refine returns an updated diff), asserting: correct
  cross-provider selection for the critique role, correct round-limit stopping, correct
  escalation trigger on exhausted rounds vs. exhausted circuit breakers.
- Roster selection: deterministic round-robin by task-slug hash, asserting the SAME task-slug
  always picks the same starting lane (reproducibility for `--resume`).
- One real live run once implemented: chain mode against a second, different FIELD-NOTES gap
  (not gap #2 again — reusing an already-fixed gap would produce a meaningless "no issues
  found immediately" result and not actually exercise a real critique/refine cycle).

## Open questions

1. Exact round-robin hash function for lane selection — any stable, deterministic hash of
   `task_slug` is fine; pick the simplest stdlib one (`hashlib.sha256(task_slug.encode()).
   digest()[0] % len(roster)`) rather than inventing a scheme.
2. Whether `garbled_output` retry should also lower that lane's priority for FUTURE tasks in
   the same session (a lightweight in-memory reliability score), or reset every invocation —
   deferred to implementation; start with per-invocation-only (simpler, and this session's
   real data is still too small to trust a persistent score yet).
3. Pattern B's consensus threshold, and whether panel lanes should see the racers' stdout
   (reasoning trace) or diff-only when ranking — both explicitly deferred to the Phase 2 spec.
