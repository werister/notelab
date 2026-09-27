# Swarm escalation loops: chain + council patterns before the frontier

## Problem

`dispatch_code_fix.py` ([redacted-private-project]) currently supports exactly one pattern: dispatch N
agents in parallel, each attempts the task independently, a human picks a winner from
whoever passes tests. That's real and it works (gap #2: kilo won a genuine 5-agent race,
merged to notelab `main`) — but it's single-shot per agent. Nothing lets one free model's
output get *improved* by another before a human ever sees it, and nothing routes "this
needs a stronger model" versus "this is fine, a human/frontier model never needs to look
at it" as a real, automated decision — every race today ends at a human review step
regardless of how clean the result was.

Sakana AI's Fugu (technical report: arxiv.org/pdf/2606.21228; see
`docs/RESEARCH-2026-09-27-sakana-fugu-learned-orchestration.md` for the fuller research
pass) is the unconstrained version of this idea: a trained 7B "conductor" model decides
per-query which frontier workers to activate, what roles to assign them, how to verify
and combine outputs, and when to synthesize a final answer — automatically, per the
ICLR 2026 "TRINITY" and "the Conductor" papers. We can't afford Fugu's approach directly
(it can freely call frontier workers per query because it bills accordingly; every call
in this system needs to stay free-tier by default) — but the *shape* is directly useful:
role assignment, verification-before-synthesis, and a real decision about when escalation
is warranted, instead of always escalating.

## Non-goals

- Not training a routing model (Fugu's actual approach). This spec is hand-coded
  heuristics over the real free-tier fleet we already have working, not a learned
  conductor. A trained router is real future scope, not this spec.
- Not automating an actual Claude API call on escalation. Every call this whole swarm
  makes today is free-tier-by-default; a script silently spending real Claude credits is
  a materially different cost decision than anything built so far and needs its own
  explicit sign-off, not a quiet addition here. "Escalate" in this spec means: produce a
  clear, structured report and stop — a human (or a live Claude Code session) decides
  whether to spend real credits on it, same as every merge decision so far in this
  session.
- Not replacing the existing parallel-race pattern (`dispatch_one`/`main()` in
  `dispatch_code_fix.py`) — both new patterns are additive, reusing the same worktree
  isolation, `AgentOutcome`, and ledger machinery already built and reviewed.
- Not solving kiro-cli's account-level monthly cap or its broken `--model` flag — those
  are real, external blockers (confirmed live this session), not something a routing
  change fixes. kiro-cli is excluded from both new patterns until that resets.

## Expanded rosters (real, verified this session — not from docs, which admit rotation)

Both new patterns need real variety within a provider to be meaningful (critiquing your
own output with the same model is weak; racing five copies of the same model isn't a
council). Confirmed live:

**kilo** (all genuinely free): `kilo-auto/free`, `nvidia/nemotron-3.5-lightning:free`,
`nvidia/nemotron-3-ultra-550b-a55b:free`, `stepfun/step-3.7-flash:free`.
(`qwen/qwen3.8-27b:free` tried and dropped — produced no response within 40s, unreliable.)

**cline**: free tier — `cline-free/deepseek-v4.1-flash`, `cline-free/muse-spark-1.3-contributor`
— plus ClinePass (flat subscription already active on this account, so marginal cost is
also $0) — `cline-pass/glm-5.3`, `cline-pass/kimi-k3`, `cline-pass/deepseek-v4-pro`,
`cline-pass/minimax-m3`, `cline-pass/qwen3.8-max`. 7 total.

**opencode** (free tier, from the 2026-09-25 handoff + this session): default
`nemotron-3.5-lightning-free` (confirmed unreliable under real load — hallucinated a
garbled shell command mid-task), `nemotron-3-ultra-free` (confirmed more careful — on a
real task it correctly recognized already-completed work and made no redundant changes),
`mimo-v2.6-flash-free`, `space-bunny-free`.

**muse**: single model (Meta), no roster — already free, already reliable, just slow
under real multi-step work (needs a longer timeout, already bumped to 420s).

This means `RunnerSpec.name` needs to carry (provider, model), not just provider — today
`"kilo"` and `"cline"` are single entries pointing at one pinned model each. Both new
patterns' RunnerSpec tables use names like `"kilo:nemotron-ultra"`,
`"cline:deepseek-v4-pro"` — the existing single-model entries in `KNOWN_RUNNERS` /
`CODE_FIX_RUNNERS` stay as-is (today's parallel-race pattern doesn't need this).

## Design — shared: the critique prompt

Both patterns need one model to review another's diff against the original task. This is
the exact shape of prompt this session has been writing by hand all along (the spec/quality
reviewer prompts in `subagent-driven-development`) — reused, not reinvented:

```
critique_prompt(task_text: str, diff: str) -> str
```

Renders: the original task text, the diff (bounded — reuse the existing 4000-char
report.md truncation convention), and an instruction to respond with exactly one of
`ISSUES: <specific, actionable list>` or `CLEAN: no issues found` — a fixed, parseable
contract, not free-form prose a script would need to interpret with an LLM call of its
own. A critique call is **read-only** (no `{cwd}`/write-trust flags needed — it only
reads the diff as text in the prompt, never touches the worktree) — reuses the existing
research-only runner shape (Component A's pattern: no `--yolo`/`--auto`/`-a`), not
`CODE_FIX_RUNNERS`'s write-trust flags. A critic never needs write access to anything.

## Design — Pattern A: implement → critique → refine chain

```
run_chain(task_prompt, target_repo, implementer: RunnerSpec, critic: RunnerSpec,
          max_rounds: int = 2) -> ChainOutcome
```

1. **Round 0 — implement**: identical to today's `dispatch_one`, one worktree, one
   branch. Records a ledger step tagged `role:implement`.
2. **Critique**: `critic` (a DIFFERENT provider than `implementer` — cross-family, same
   principle `consensus_gate.sh` already enforces as "Law 2": no single-family rubber
   stamp) reads the diff via the critique prompt above. Records a ledger step tagged
   `role:critique`, `loop_pattern:chain`.
3. **Refine** (only if critique returned `ISSUES:`): re-invoke `implementer` in the SAME
   worktree/branch, prompt = original task + "a reviewer found: <issues>, please
   address them." Records `role:refine`.
4. Repeat critique → refine up to `max_rounds` (default 2 — bounds real cost/time; this
   is a knob, not a hardcoded constant, exposed via CLI).
5. **Converged** (tests pass AND latest critique is `CLEAN:`) → done, never escalated.
   **Not converged after max_rounds** → escalate: write a report naming the unresolved
   critique + the final diff + the full round history, exit distinctly (not a plain
   pass/fail) so a human/live session knows this needs frontier judgment, not just "it
   failed."

**Circuit breakers** (from real failure modes observed this session, not hypothetical):
- **Hard quota exhaustion** (kiro-cli's account-level monthly cap): the FIRST hard-quota
  error from any provider marks that provider `unavailable` for the rest of the process
  run (an in-memory set, not a config file) — never retried against the same dead
  account mid-run.
- **Garbled/gibberish output**: a cheap heuristic — the response contains an obvious
  shell parse error string (`"parse error"`, `"syntax error"`) or a implausibly high
  ratio of non-ASCII characters in what should be prose/code — triggers ONE automatic
  retry on a DIFFERENT model from the same provider's roster (never the same model
  again; opencode's `nemotron-3.5-lightning-free` garbling isn't going to un-garble on
  retry). A second garble from the retry escalates immediately rather than looping.
- **Per-model timeout**: default stays `DEFAULT_TIMEOUT` (420s), but the roster can carry
  a per-entry override — muse and opencode get more room; kilo (123s to a full working
  fix in the real race) doesn't need as much.

## Design — Pattern B: parallel race + cross-vote council

```
run_council(task_prompt, target_repo, implementers: list[RunnerSpec],
           judges: list[RunnerSpec], consensus_threshold: float = 0.6) -> CouncilOutcome
```

1. **Race**: identical to today's `main()`/`dispatch_one` loop — all `implementers` run
   concurrently, each in its own worktree, test-gated exactly as today.
2. **Vote**: for every candidate that produced a non-empty diff (regardless of whether
   ITS OWN test run passed — a candidate that failed its own environment step might still
   be a good diff worth a second opinion, matching the `could_not_run` honesty already
   built), each `judge` (disjoint from `implementers` — a model doesn't judge its own or
   a same-provider sibling's output, same cross-family principle as the chain pattern)
   independently picks the best candidate or "none acceptable," via the same critique
   prompt extended to a comparison: given task + all candidate diffs (labeled A/B/C...,
   not by agent name — blind voting, no popularity bias toward a model a judge
   "recognizes"), respond `VOTE: <letter>` or `VOTE: none`.
3. **Consensus**: if a candidate gets >= `consensus_threshold` of judge votes AND passed
   its own tests → merge-ready, no escalation. Otherwise (split vote, or the top-voted
   candidate didn't pass tests) → escalate with the full race + vote record.
4. Every vote is its own ledger step, tagged `role:vote`, `loop_pattern:council`,
   recording which candidate that judge picked (not just the final tally) — this is the
   real per-hop data the dashboard needs, not a collapsed summary.

Same circuit breakers as Pattern A (hard-quota provider exclusion, garble-retry-once,
per-model timeout).

## Ledger data — what the dashboard actually gets

Every hop (implement/critique/refine/vote) is its own `session.record_step()` call,
`tags` including a `role:*` and `loop_pattern:*` entry, `decision_reason` naming the round
number and (for refine/vote) what the previous hop found. This directly extends the
`exceeded_scope_positive` capability already shipped: a human reviewing a chain or council
run can call `annotate_exceeded_scope()` on any individual hop, not just the final
outcome — e.g. tagging a *critique* that caught something real, not just an implementation.

A future dashboard query over this data can show, per provider/model: real acceptance
rate, how often it's picked as `implementer` vs. `critic`/`judge`, how often its critiques
get acted on vs. ignored, and how often a chain/council converges without ever escalating
— the actual "which routing works under what circumstances" signal this whole session's
swarm work has been building toward.

## Testing

- `critique_prompt()`: pure string-building, deterministic unit tests (task+diff in,
  exact expected prompt shape out).
- `run_chain()`: fully mocked (fake `run_adversary` returning scripted
  ISSUES/CLEAN/garbled responses per call) — tests: converges round 1 (clean critique,
  no refine needed); converges round 2 (one refine round, then clean); escalates after
  max_rounds with unresolved issues; garble triggers exactly one retry then escalates on
  a second garble; hard-quota error excludes that provider for the rest of the same
  process (a second dispatch to it is never attempted).
- `run_council()`: fully mocked — tests: clear majority merges; split vote escalates;
  top-voted candidate failing its own tests escalates even with a majority vote; a judge
  never votes for a same-provider candidate (assertion on call arguments, not just
  output).
- Both: real live run smoke test is a LATER, separate step (matching how the original
  parallel-race pattern went: build + test offline first, then one real supervised run) —
  not part of this implementation plan's automated test suite.

## Open questions

1. `consensus_threshold` default (0.6, i.e. 3/5 judges) is a starting guess, not
   validated against real data yet — the first live council run is exactly what would
   calibrate this, same as `DEFAULT_TIMEOUT` got tuned from the gap #2 race's real
   numbers.
2. Whether `judges` for Pattern B should include models NOT in `implementers` at all
   (dedicated judge-only roster) or just be "whichever implementers didn't produce a
   diff this round" — the spec above assumes a separate, explicit `judges` list; simpler
   but less automatic. Worth revisiting once real council data exists.
3. Garble detection is a cheap heuristic (parse-error strings, non-ASCII ratio), not a
   real classifier — it will have false negatives (a genuinely garbled response that
   doesn't happen to contain those markers) and possibly rare false positives (legitimate
   code with unusual characters). Documented as a known limitation, not solved here.
