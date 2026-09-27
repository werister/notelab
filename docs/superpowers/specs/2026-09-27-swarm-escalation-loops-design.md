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

**Revised after eng review** (P0, verified live: `git check-ref-format --branch
"swarm/kilo:nemotron-ultra/x"` → `fatal: not a valid branch name` — a literal colon in
`RunnerSpec.name` breaks `create_worktree`'s `f"swarm/{agent}/{task_slug}"` branch name
before any agent ever runs; the same colon-splitting ambiguity also breaks the
same-provider/cross-family checks below, since real model ids already contain colons,
e.g. `nvidia/nemotron-3.5-lightning:free`).

Fix: `RunnerSpec` (`notelab/research/evals.py`) gets one new field,
`provider: str = ""` (a default-valued addition — every existing single-model
`KNOWN_RUNNERS`/`CODE_FIX_RUNNERS` entry is unaffected, `provider` just stays `""` for
them since today's parallel-race pattern never reads it). Roster entries set BOTH a
branch-safe `name` (hyphens only, no colons or slashes — e.g. `"kilo-nemotron-ultra"`,
`"cline-deepseek-v4-pro"`) and an explicit `provider` (e.g. `"kilo"`, `"cline"`) used for
every same-provider/cross-family decision in this spec — never derived by splitting
`name` or `bin` at runtime.

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
2. **Critique**: `critic` (`critic.provider != implementer.provider` — cross-family, same
   principle `consensus_gate.sh` already enforces as "Law 2": no single-family rubber
   stamp) reads the diff via the critique prompt above. Records a ledger step tagged
   `role:critique`, `loop_pattern:chain`.
3. **Refine** (only if critique returned `ISSUES:`): re-invoke `implementer` in the SAME
   worktree/branch, prompt = original task + "a reviewer found: <issues>, please
   address them." Records `role:refine`, with `Attribution.correction_rounds` set to the
   current round number (see "Ledger data" below).
4. Repeat critique → refine up to `max_rounds` (default 2 — bounds real cost/time; this
   is a knob, not a hardcoded constant, exposed via CLI).
5. **Converged** (tests pass AND latest critique is `CLEAN:`) → done, never escalated.
   **Not converged after max_rounds** → escalate: write a report naming the unresolved
   critique + the final diff + the full round history, exit distinctly (not a plain
   pass/fail) so a human/live session knows this needs frontier judgment, not just "it
   failed."

**Circuit breakers** (from real failure modes observed this session, not hypothetical).
**Revised after devex review**: both breakers below now print an explicit `stderr` line
when they fire, matching the base script's existing loud-stderr house style (e.g. the
worktree-pre-check and `--resume` errors) — previously they were silent in-process state
changes, so a human debugging a run at 3am had no way to tell "this escalated because of
a circuit breaker" from "this escalated because nobody converged" short of a raw SQLite
query against the ledger. Both are ALSO recorded as ledger tags
(`circuit_breaker:hard_quota`, `circuit_breaker:garble_retry`) so they're queryable, not
just printed:
- **Hard quota exhaustion** (kiro-cli's account-level monthly cap): the FIRST hard-quota
  error from any provider marks that provider `unavailable` for the rest of the process
  run (an in-memory set, not a config file) — never retried against the same dead
  account mid-run. Prints `[circuit-breaker] {agent} marked unavailable for the rest of
  this run: {reason}` to stderr the moment it fires. (P2 note: under `ThreadPoolExecutor`
  in the council pattern, the GIL makes the set itself thread-safe, but two
  near-simultaneous dispatches to the same dying provider could both start before either
  sees the exclusion — a benign race, not a correctness bug, since both just fail the
  same real way.)
- **Garbled/gibberish output**: a cheap heuristic — the response contains an obvious
  shell parse error string (`"parse error"`, `"syntax error"`) or a implausibly high
  ratio of non-ASCII characters in what should be prose/code — triggers ONE automatic
  retry on a DIFFERENT `RunnerSpec` with the SAME `.provider` (never the same `.name`
  again; opencode's `nemotron-3.5-lightning-free` garbling isn't going to un-garble on
  retry). Prints `[circuit-breaker] {agent}'s output looked garbled, retrying once on
  {fallback.name}` to stderr. A second garble from the retry escalates immediately
  rather than looping (and prints why).
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
   built), each `judge` (`judge.provider` disjoint from every candidate's `.provider` it's
   voting on — a model doesn't judge its own or a same-provider sibling's output, same
   cross-family principle as the chain pattern) independently picks the best candidate or
   "none acceptable," via the same critique
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

**Revised after eng review** (P1: the original draft encoded round number and
prior-hop findings only in free-text `decision_reason` + tags — but the schema already
has structured fields built for exactly this, `Attribution.correction_rounds` and
`RoutingMetadata.escalated_from_previous_step`, both live-consumed by
`timevalue.py`'s penalty math and rendered in `cli_report.py`/`dashboard.py`/`cli.py`.
Skipping them would make chain/council runs invisible to every existing dashboard/
time-value view that already reads them from every other ledger entry).

Every hop (implement/critique/refine/vote) is its own `session.record_step()` call:
- `Attribution.correction_rounds` = the current round number (0 for the initial
  implement/race step, incrementing per refine round or vote pass).
- `RoutingMetadata.escalated_from_previous_step` = `True` for any step that exists
  BECAUSE a prior hop didn't converge/reach consensus (a refine step following an
  `ISSUES:` critique; the final escalation report step itself) — `False` for the
  initial implement/race step and for a refine that never happens because the first
  critique was already `CLEAN:`.
- `tags` still carries a `role:*` and `loop_pattern:*` entry (implement/critique/
  refine/vote; chain/council) — tags are for filtering/display, the two fields above
  are for the numeric time-value/penalty math that already exists and already expects
  them.
- `decision_reason` stays free-text (what the previous hop actually found), same as
  today — structured fields and a human-readable reason aren't a tradeoff, both belong
  on the same step.

This directly extends the
`exceeded_scope_positive` capability already shipped: a human reviewing a chain or council
run can call `annotate_exceeded_scope()` on any individual hop, not just the final
outcome — e.g. tagging a *critique* that caught something real, not just an implementation.

A future dashboard query over this data can show, per provider/model: real acceptance
rate, how often it's picked as `implementer` vs. `critic`/`judge`, how often its critiques
get acted on vs. ignored, and how often a chain/council converges without ever escalating
— the actual "which routing works under what circumstances" signal this whole session's
swarm work has been building toward.

## Design — CLI surface and escalation reporting

**Added after devex review** (the original draft gave `run_chain()`/`run_council()` as
Python signatures only — no flag, subcommand, or mapping onto the existing `--agents`
convention an operator already knows from the parallel-race pattern; no exit code or
report path for the new escalation shape, recurring the same "report persistence" gap
the 2026-09-26 devex review already caught once for the original per-agent report).

**CLI**: a new `--pattern {race,chain,council}` flag on `dispatch_code_fix.py`, default
`race` (today's existing, unchanged behavior — fully backward compatible, no flag needed
for what already works):
```
# chain: one implementer, one critic, explicit roster names (the git-safe ones, see
# "Expanded rosters" above) -- reuses --timeout, adds --max-rounds
dispatch_code_fix.py --pattern chain --target-repo ... --task-slug ... --prompt-file ... \
    --implementer kilo-nemotron-ultra --critic cline-deepseek-v4-pro --max-rounds 2

# council: reuses --agents for the racing implementers (exactly as today), adds --judges
# (comma-separated, same roster-name convention) and --consensus-threshold
dispatch_code_fix.py --pattern council --target-repo ... --task-slug ... --prompt-file ... \
    --agents kilo-nemotron-ultra,cline-deepseek-v4-pro,opencode-ultra \
    --judges muse,kilo-step-flash --consensus-threshold 0.6
```

**Escalation reporting**: chain/council runs that don't converge/reach consensus exit
with code **2** (distinct from `0` = converged/merge-ready and `1` = a hard error like
bad args or `git worktree add` failing outright — `2` specifically means "ran to
completion but needs frontier/human judgment," a real, scriptable signal). The report
doesn't fit the existing per-agent `write_report()` shape (a chain has one diff plus a
round history; a council has N diffs plus a vote tally) — new, separate report files
under the same `.swarm-runs/<task_slug>/` directory convention:
`chain-report.{md,json}` (round-by-round: implement/critique/refine transcript, final
diff, which critique issues stayed unresolved) and `council-report.{md,json}` (every
candidate's diff + test result, every judge's vote, the tally, why consensus wasn't
reached if it wasn't).

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
- CLI: `--pattern race` (or omitted) behaves identically to today, unchanged (regression
  test against the existing parallel-race test suite). `--pattern chain`/`council` parse
  `--implementer`/`--critic`/`--judges`/`--max-rounds`/`--consensus-threshold` correctly
  and route to `run_chain`/`run_council` (mocked, no real dispatch).
- Escalation exit codes: a mocked non-converging chain/council run exits `2`; a mocked
  converging one exits `0`; a bad-args case (e.g. `--pattern chain` with no `--critic`)
  exits `1` with a clear stderr message, not a stack trace.
- Circuit-breaker stderr lines: capture stderr in the garble-retry and hard-quota-exclusion
  mocked tests, assert the `[circuit-breaker]` line is actually printed, not just that the
  retry/exclusion behavior happens silently-but-correctly.

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

## DX Review (plan-devex-review)

### Developer Persona Card

Reused from the prior DX review of the sibling design doc (2026-09-26, score 4/10,
mode DX POLISH) — same persona, same tool, additive scope:

```
TARGET DEVELOPER PERSONA
========================
Who:       Solo maintainer of this notelab/[redacted-private-project] swarm tooling, no live support,
           no teammates who'll ever run this — the same person who writes the spec,
           implements it, and debugs it at odd hours.
Context:   Invokes dispatch_code_fix.py (or its chain/council extensions) from a
           terminal, either kicking off a real task or coming back to a run that
           finished, hung, or escalated while they were away.
Tolerance: Near-zero patience for silent failure. High tolerance for reading raw
           SQLite/JSON if the CLI surface itself is honest about state.
Expects:   The same flag shape, exit-code discipline, and report-file convention
           dispatch_code_fix.py already established (`--target-repo --task-slug
           --prompt-file --agents --resume --timeout --cleanup --db`,
           `.swarm-runs/<task_slug>/report.{md,json}`, one ledger DB).
```

### Developer Empathy Narrative

It's 3am. A council run kicked off before bed on a real task, `consensus_threshold`
0.6, five implementers, three judges. It escalated — split vote. I open the terminal
expecting `dispatch_code_fix.py`'s familiar closing block (`[PASS/FAIL] agent status
tests elapsedms`, then `oracle_best_model:`). Nothing in the spec says Pattern B
prints anything like that, or what it prints instead. I go looking for the escalation
report. The base tool always writes `.swarm-runs/<task_slug>/report.md` — does the
council write there too, or somewhere else, since its shape (candidates A/B/C, per-judge
votes, tally) doesn't fit `write_report()`'s per-agent PASS/FAIL loop? I don't know,
because the spec never says. I go to the ledger next, since "every vote is its own
`record_step()`" was the promise — but there's no query command, so it's `sqlite3
~/.local/state/[redacted-private-project]/swarm-runs.db` and hand-written SQL against `tags` and
`decision_reason` strings at 3am. I find the votes. I still can't tell, without reading
source, whether the garble circuit-breaker fired on any candidate this run, because
`decision_reason` isn't specified to name which heuristic tripped (parse-error string
vs. non-ASCII ratio) or on what raw text. I have working data, but reconstructing "why
did this escalate" costs real archaeology, not a glance.

### Competitive DX Benchmark

`Note: options differ in kind, not coverage — no completeness score.` This is a
single-operator internal CLI extending an already-merged pattern, not a product with
external adopters — Stripe/Vercel-tier TTHW benchmarking doesn't apply and running it
would manufacture false signal. The only real competitor is the base
`dispatch_code_fix.py` CLI itself: does Pattern A/B feel like the same tool, or a
different one bolted on? (See Pass 1/2 below — this is the actual comparison that
matters here.)

### Magical Moment Specification

Not applicable in the Stripe-instant-API sense. The nearest equivalent for this
persona: a chain/council run that converges cleanly prints the same unambiguous
`[PASS]` signal the race pattern already gives, and an escalated run prints an
equally unambiguous "here's exactly why, here's exactly where the report is" —
matching the existing tool's own DEFAULT_TIMEOUT-tuning-from-real-numbers ethos.
This is Pass 1 + Pass 3's job, not a separate vehicle to design.

### Developer Journey Map (DX TRIAGE: Install + Debug only, per mode below)

```
STAGE           | DEVELOPER DOES                          | FRICTION POINTS         | STATUS
----------------|------------------------------------------|--------------------------|--------
Install         | N/A — same binary, same repo, no new     | none — correctly reuses  | ok
                | install step                              | existing worktree infra  |
Invoke (new)    | Wants to run Pattern A or B from the CLI  | spec defines Python      | deferred
                | the way they run the race today           | signatures only, no CLI  | (Pass 2)
                |                                            | flags at all             |
Debug/escalate  | Comes back to a hung/escalated run, needs | no defined report path   | deferred
                | to know why + read the round history       | for chain/council; no    | (Pass 3)
                |                                            | ledger query tool; no    |
                |                                            | required stdout summary  |
                |                                            | line on escalation       |
```

### First-Time Developer Confusion Report

```
FIRST-TIME DEVELOPER REPORT (this persona, this spec)
=======================================================
Attempting: run Pattern B (council) on a real task after implementing this spec.

T+0:00  Look for the CLI entry point. Spec gives `run_council(task_prompt,
        target_repo, implementers, judges, consensus_threshold) -> CouncilOutcome`
        — a function signature, not a command. Nothing says whether this becomes a
        new script, a new subcommand of dispatch_code_fix.py, or a `--pattern`
        flag on the existing one.
T+0:30  Guess it's a `--pattern council` flag by analogy with `--cleanup`. Nothing
        in the spec confirms or denies how `implementers`/`judges` map to
        `--agents` — the base CLI's `--agents` takes bare names ("kilo,cline");
        the spec's RunnerSpec names for new patterns are "provider:model"
        composites ("kilo:nemotron-ultra"). Two different addressing schemes
        under one flag, or two new flags? Unspecified.
T+2:00  Run kicks off (however it's invoked). It escalates. Terminal output shape
        unspecified — does it match the race's `[PASS/FAIL] agent ... elapsedms`
        block, or something new?
T+3:00  Go find the report. `.swarm-runs/<task_slug>/report.md` is the existing
        convention (one shape: per-agent diff blocks). Council output is a
        different shape (candidates, per-judge votes, tally). Same file? New
        file? New directory? Not addressed — this is the exact "report
        persistence" gap the PRIOR devex review (2026-09-26 doc) already caught
        and fixed for the base script's single output; the fix doesn't
        automatically extend to a structurally different output shape.
T+4:00  Escalation exit code: spec says "exit distinctly (not a plain pass/fail)"
        but never names the code. `main()` today only ever returns 0 on a
        completed race (0/1 is reserved for argument errors) — a caller scripting
        around this (cron, another agent) has nothing to branch on yet.
Outcome: Succeeded eventually by reading source, not by the spec being CLI-complete.
```

### "What already exists" (reuse, don't reinvent)

- `--agents` comma-separated subset filtering, `--resume` conflict handling,
  `--cleanup`, `--timeout`, `--db` — established flag vocabulary the new patterns
  should extend, not sidestep.
- `write_report()` → `.swarm-runs/<task_slug>/{report.md,report.json}` — established
  report location and dual-format (human `.md` + machine `.json`) convention.
- `record_ledger()` / `Session.record_step()` via `redacted_private_project.routing_monitor.ledger` —
  already the right place for `role:*`/`loop_pattern:*` tags per the spec's own design.
- `annotate_exceeded_scope()` — already demonstrates the append-only, human-annotates-
  a-past-step pattern the spec explicitly wants to extend to chain/council hops.
- Loud stderr `print(f"error: ...")` on every existing failure path (worktree exists,
  worktree create failure with rollback) — the tool already has a house style for
  surfacing failure; the spec's circuit breakers (hard-quota exclusion, garble retry)
  don't yet commit to following it.

### "NOT in scope" (explicitly deferred, one-line rationale each)

- Stripe/Vercel-tier TTHW benchmarking — single-operator internal tool, no external
  adopters; would manufacture false signal (see Competitive DX Benchmark above).
- Community/ecosystem, upgrade/migration path, TypeScript/editor integration passes —
  not applicable to a solo-maintainer Python CLI with no consumers besides its author.
- A learned/automated router — explicitly out of scope in the spec's own Non-goals.

### Pass 1: Getting Started Experience (CLI invocation) — score 3/10

The spec defines `run_chain()`/`run_council()` as Python function signatures only.
There is no `--pattern` flag, no subcommand, no mention of how `implementer`/`critic`/
`judges` RunnerSpecs get selected from the command line at all. An operator who knows
`dispatch_code_fix.py --agents opencode,muse --target-repo ... --task-slug ...` today
has zero guidance on how to invoke Pattern A or B — not "hard to find," genuinely
undefined. This is the single highest-leverage gap in the spec: everything else
(circuit breakers, ledger tags) is meaningless if there's no committed entry point.

FIX TO 10: name the actual CLI surface before implementation starts — e.g.
`--pattern {race,chain,council}` (default `race`, preserving today's behavior with
zero flag changes), `--implementer` / `--critic` (chain), `--implementers` /
`--judges` (council, comma-separated, reusing `--agents`' existing splitting
convention), `--max-rounds`, `--consensus-threshold`. This is a naming decision, not
an implementation detail — it belongs in the spec, the same way the spec already
nails down `critique_prompt()`'s exact contract.

### Pass 2: CLI/API Design (consistency with existing flags) — score 4/10

- `RunnerSpec.name` becoming a "provider:model" composite for new patterns while
  staying a bare provider name for `KNOWN_RUNNERS`/`CODE_FIX_RUNNERS` is a real,
  intentional design choice per the spec — but it creates two addressing schemes
  active in the same binary. `--agents kilo,cline` (race) and whatever selects
  `kilo:nemotron-ultra` as `implementer` (chain) look like different tools to
  someone who's only ever run the race. One line in the spec confirming the CLI
  flag(s) that carry the composite form would close this.
- `max_rounds` and `consensus_threshold` are correctly specified as function
  params, not hardcoded constants — matches `DEFAULT_TIMEOUT`'s existing pattern of
  being a real, overridable knob. Good; no change needed once wired to a flag.
- Judges/critic being cross-family from implementers (Law 2) is enforced at the
  design level but the spec doesn't say what happens on CLI misuse — e.g. an
  operator passes `--critic kilo:nemotron-ultra --implementer kilo:kilo-auto`
  (same provider). Silent downgrade, hard error, or ignored? Undefined; should be a
  hard error at argument-parsing time, matching the tool's existing house style of
  failing loud on `--resume`-less worktree collisions rather than proceeding.

### Pass 3: Error Messages & Debugging (the 3am case) — score 3/10

Traced three specific paths from the spec text against the existing tool's error
conventions:

1. **Hard-quota exclusion.** Spec: "an in-memory set... never retried against the
   same dead account mid-run." No mention of a printed/logged line when a provider
   gets excluded. Existing tool precedent: every failure path prints to stderr
   (`error: {e}`, rollback count). An operator watching a long chain/council run
   won't know provider X went dark unless this circuit breaker follows the same
   loud-by-default house style. FIX: print (and tag in the ledger step, not just
   `decision_reason` prose) the exact trigger — "kiro-cli: hard-quota error, excluded
   for remainder of run" — the moment it fires, not just reconstructable after the
   fact from ledger archaeology.
2. **Garble retry.** Spec names two detection heuristics (parse-error string,
   non-ASCII ratio) but never commits to recording WHICH one fired, on what raw
   text, in the ledger step tagged `role:*`. Without that, a human auditing a
   council run that escalated after a second garble has to guess whether it was a
   real garble or a false positive (a stated, accepted risk in Open Question #3) —
   the one piece of data that would let them tell the difference isn't captured.
   FIX: `decision_reason` should name the heuristic and include a bounded excerpt
   of the flagged text, the same way `AgentOutcome.stdout_tail`/`stderr_tail`
   already truncate-and-keep raw evidence rather than collapsing to a verdict.
3. **Escalation itself.** Spec: "exit distinctly (not a plain pass/fail)." No exit
   code named, no stdout summary line specified, no defined report file path for
   the chain/council-shaped output (round history / vote tally don't fit
   `write_report()`'s per-agent diff-block format). This is the same "report
   persistence" category the prior (2026-09-26) devex review caught and got fixed
   for the base script's one report shape — it recurs here because the new output
   shapes are genuinely different, not because the earlier fix was undone.
   FIX: define the exit code (e.g. `2` = escalated, distinct from `0` = converged/
   merge-ready and `1` = argument/setup error) and the report path convention
   (`.swarm-runs/<task_slug>/chain-report.md` / `council-report.md`, or a
   `pattern` field inside the existing `report.json` shape) in the spec itself.

### Pass 4: Documentation & Learning — score 4/10 (No new findings beyond Pass 1/3)

The spec's own prose is the only documentation surface for this tool (no separate
docs/ tree) and it's already dense and well-grounded in real failure modes — the gap
isn't explanatory quality, it's the missing CLI contract covered above.

### Pass 5: Upgrade & Migration Path — No issues found

Pure addition; `KNOWN_RUNNERS`/`CODE_FIX_RUNNERS` and today's `--agents` behavior are
explicitly untouched per the spec's own Non-goals. Nothing to migrate.

### Pass 6: Developer Environment & Tooling (observability) — score 3/10

The single biggest recurring theme across Pass 1/3 belongs here too: there is no
ledger query tool. `record_step()` writing per-hop `role:*`/`loop_pattern:*` tags is
the right data model, but reading it back today means raw SQL against `LedgerStore`'s
schema. The spec explicitly plans a "future dashboard query" over this data (Ledger
data section) but that's future scope — meanwhile every chain/council run this spec
ships makes the un-queryable pile bigger. A single `--dump-ledger <task-slug>` or
equivalent (even a thin `sqlite3 ... | jq` wrapper script) would turn "3am SQL
archaeology" into "3am `cat`." Not blocking for a first implementation, but the spec
should at minimum say this is deferred, not silently omit it — track it as a TODO
(below) rather than let it fall through the crack the prior review already flagged
for this same ledger machinery.

### Pass 7: Community & Ecosystem — No issues found (not applicable, solo tool)

### Pass 8: DX Measurement & Feedback Loops — score 5/10

The spec's own "Ledger data" section already commits to the right long-term
measurement (per-provider acceptance rate, implementer vs. critic/judge performance,
convergence-without-escalation rate) — genuinely ahead of most internal tooling specs
on this dimension. The gap is purely operational: none of that is queryable without
the Pass 6 tool, and Open Question #1 (`consensus_threshold` default) explicitly
depends on having read this data back after a real run, so the measurement design and
the read-back tooling are coupled in a way the spec doesn't call out.

### DX Scorecard

```
+====================================================================+
|              DX PLAN REVIEW — SCORECARD                             |
+====================================================================+
| Dimension            | Score  | Prior  | Trend  |
|----------------------|--------|--------|--------|
| Getting Started      |  3/10  |  N/A   |   —    |
| API/CLI/SDK          |  4/10  |  N/A   |   —    |
| Error Messages       |  3/10  |  N/A   |   —    |
| Documentation        |  4/10  |  N/A   |   —    |
| Upgrade Path         |  N/A   |  N/A   |   —    |
| Dev Environment      |  3/10  |  N/A   |   —    |
| Community            |  N/A   |  N/A   |   —    |
| DX Measurement       |  5/10  |  N/A   |   —    |
+--------------------------------------------------------------------+
| TTHW                 |  N/A — internal CLI, not benchmarked         |
| Competitive Rank     | N/A (no external competitor; see benchmark)  |
| Magical Moment       | N/A — operability clarity is the target, see |
|                       | Pass 1/3                                     |
| Product Type         | CLI Tool / internal dev-ops script           |
| Mode                 | DX TRIAGE                                    |
| Overall DX           |  4/10  |  4/10  |  → flat |
+====================================================================+
| DX PRINCIPLE COVERAGE                                               |
| Zero Friction      | gap — no defined CLI entry point for A/B       |
| Learn by Doing     | n/a — no interactive surface                   |
| Fight Uncertainty  | gap — circuit breakers not required to log     |
| Opinionated + Escape Hatches | covered — max_rounds/threshold are   |
|                       real knobs, not hardcoded                     |
| Code in Context    | covered — spec grounded in real failure modes  |
| Magical Moments    | n/a — see Magical Moment Specification above   |
+====================================================================+
```

The prior sibling-doc review (2026-09-26) scored overall DX 4/10 with unresolved CLI/
observability concerns for the base script; this spec inherits that baseline flat
rather than improving it, because it adds two new patterns' worth of surface without
yet committing to the CLI/report/exit-code contract that would close the prior gaps.

### DX Implementation Checklist

```
DX IMPLEMENTATION CHECKLIST (this spec)
========================================
[ ] CLI entry point defined for Pattern A and Pattern B (--pattern flag or
    subcommand; --implementer/--critic/--implementers/--judges flags)
[ ] --max-rounds / --consensus-threshold wired to real CLI flags, not just
    Python defaults
[ ] Cross-family enforcement (Law 2) fails loud at argument-parse time on misuse
[ ] Hard-quota exclusion prints a visible line the moment it fires, not just a
    reconstructable ledger row
[ ] Garble-retry decision_reason names the heuristic + a bounded raw-text excerpt
[ ] Escalation gets a distinct, documented exit code (e.g. 2)
[ ] Escalation report file path defined for chain/council's non-per-agent shape
[ ] Ledger read-back tool (even minimal) tracked as a TODO if not built now
```

## Implementation Tasks
Synthesized from this review's findings. Each task derives from a specific finding
above. Run with Claude Code or Codex; checkbox as you ship.

- [ ] **T1 (P1, human: ~1h / CC: ~10min)** — CLI — Define `--pattern {race,chain,council}`
  plus `--implementer`/`--critic`/`--implementers`/`--judges`/`--max-rounds`/
  `--consensus-threshold` flags before implementing `run_chain`/`run_council`.
  - Surfaced by: Pass 1 — no CLI entry point exists for either new pattern.
  - Files: this spec (add a "CLI surface" section); `dispatch_code_fix.py` (or its
    successor) at implementation time.
  - Verify: an operator can invoke Pattern A/B using the same `--agents`-style
    comma-splitting convention as the race pattern, no source-reading required.
- [ ] **T2 (P1, human: ~30min / CC: ~10min)** — Reporting — Define the escalation
  report file path/shape and a distinct exit code for chain/council runs.
  - Surfaced by: Pass 3 finding 3 — report persistence gap recurring for the new
    output shapes; no exit code named for "exit distinctly."
  - Files: this spec; `write_report()`'s successor at implementation time.
  - Verify: `echo $?` after an escalated run is distinct from 0 (converged) and 1
    (argument error); the report file exists at a documented path.
- [ ] **T3 (P2, human: ~20min / CC: ~5min)** — Circuit breakers — Log
  hard-quota exclusion and the garble-retry heuristic/excerpt at the moment they
  fire, matching the tool's existing loud-stderr house style.
  - Surfaced by: Pass 3 findings 1-2.
  - Files: circuit-breaker implementation (new in this spec); ledger
    `decision_reason` construction.
  - Verify: a run with a forced garble or a simulated hard-quota error prints a
    human-readable line naming the trigger, not just a ledger row.
- [ ] **T4 (P3, human: ~2h / CC: ~30min)** — Tooling — Minimal ledger read-back
  (`--dump-ledger <task-slug>` or equivalent) so per-hop data doesn't require raw
  SQL to inspect.
  - Surfaced by: Pass 6/8 — no query tool exists; the spec's own measurement goals
    depend on being able to read this data back.
  - Files: new small script/flag; `redacted_private_project.routing_monitor.ledger` read path.
  - Verify: a chain/council run's role/loop_pattern-tagged steps are readable via
    one command, not a hand-written SQL query.

### Unresolved Decisions
1. Whether Pattern A/B get a `--pattern` flag on the existing `dispatch_code_fix.py`
   or a separate script — a real architectural choice with CLI-consistency
   consequences (T1), not resolvable without the author's input.
2. Whether the escalation report reuses `report.json`'s shape (a `pattern` field
   distinguishing race/chain/council) or gets its own file naming convention (T2).
3. Whether the ledger read-back tool (T4, P3) is worth building now or explicitly
   deferred to the "future dashboard" the spec already anticipates — a scope call,
   not a correctness issue.

## GSTACK REVIEW REPORT

**VERDICT: DONE_WITH_CONCERNS — one P0 must be fixed before this becomes an implementation
plan; two P1s should be resolved in the same pass.**

Grounded against the real code (not just the spec text): `[redacted-private-project]/.worktrees/swarm-dispatch/examples/dispatch_code_fix.py`,
`src/redacted_private_project/routing_monitor/ledger/schema.py`, `src/redacted_private_project/routing_monitor/session.py`,
`src/consensus_gate.sh`, and notelab's `src/notelab/research/evals.py`.

| Review | Trigger | Why | Runs | Status | Findings |
|---|---|---|---|---|---|
| Architecture | design doc | shared chain/council reuse of worktree/ledger machinery | 1 | Fixed | Colon-bearing `RunnerSpec.name` broke git branch creation — fixed (git-safe names + explicit `provider` field) |
| Code Quality | design doc | critique contract, circuit breakers, provider extraction | 1 | Fixed | Provider extraction now an explicit field, not parsed from a display name |
| Tests | design doc | mocked test plan in "Testing" section | 1 | Adequate | Covers the documented paths; P0/P1/DX cases added below |
| Performance | design doc | concurrent circuit-breaker state | 1 | Minor | In-memory hard-quota set under `ThreadPoolExecutor`, low risk, noted inline |
| DX Review | `/plan-devex-review` (separate agent, findings below — its own file write was lost to a concurrent-write race with the eng review, same as the 2026-09-26 spec's review round) | CLI discoverability, 3am debuggability | 1 | Fixed | No CLI surface, silent circuit breakers, underspecified escalation report — all three fixed, see "Revision after eng + devex review" |

**[P0] (confidence: 10/10)** `RunnerSpec.name` examples given in "Expanded rosters" —
`"kilo:nemotron-ultra"`, `"cline:deepseek-v4-pro"` — contain a literal colon. Both new
patterns reuse `dispatch_one`/`create_worktree` verbatim ("identical to today's
`dispatch_one`", "identical to today's `main()`/`dispatch_one` loop"), and
`dispatch_code_fix.py:98-99` builds the git branch as
`f"swarm/{agent}/{task_slug}"` where `agent = spec.name`. Git rejects colons in ref
names — verified live: `git check-ref-format --branch "swarm/kilo:nemotron-ultra/x"`
→ `fatal: not a valid branch name`. Every chain/council run using the roster names
exactly as written in this spec will fail at `git worktree add -b ...` for kilo and
cline entries, before the agent ever runs. Fix: pick a git-safe separator (`kilo__nemotron-ultra`
or `kilo-nemotron-ultra`), or add a dedicated `provider` field to `RunnerSpec` instead
of overloading `name`, and use a sanitized form for branch/worktree paths specifically.

**[P1] (confidence: 8/10)** Nothing in the design specifies how `run_chain`/`run_council`
recover a bare "provider" from a composite name for: (a) the circuit breaker's "retry on
a DIFFERENT model from the same provider's roster", (b) the cross-family constraint
("critic a DIFFERENT provider than implementer", "a judge never votes for a same-provider
candidate"). A naive `name.split(":")[0]` is implied but never stated — and is doubly
fragile since several real model IDs in the roster already contain colons
(`nvidia/nemotron-3.5-lightning:free`), so the split delimiter collides with data. Needs
an explicit `provider` attribute (see P0 fix) that both the branch-naming code and the
circuit-breaker/cross-family logic read from directly, not parse out of a display name.

**[P1] (confidence: 8/10)** The "Ledger data" section proposes encoding round number and
prior-hop findings only in `RoutingMetadata.decision_reason` (free text) plus custom
`tags`. The schema already has two structured fields built for exactly this:
`Attribution.correction_rounds` (int) and `RoutingMetadata.escalated_from_previous_step`
(bool) — both are live-consumed today, not decorative: `correction_rounds` feeds
`timevalue.py`'s `cumulative_delay` round-penalty math and is aggregated at the session
level (`session.py:247`) and rendered in `cli_report.py:277` /
`dashboard.py:656`; `escalated_from_previous_step` gates a display line in
`routing_monitor/cli.py:64`. A chain's refine hop (round ≥1) or council's escalate-to-report
step should set `escalated_from_previous_step=True` and `correction_rounds=<round>` on the
`Attribution`/`RoutingMetadata` passed to `record_step`, not only free-text `decision_reason`.
Skipping this makes chain/council runs invisible to the existing time-value penalty and
dashboard correction-rounds displays that every other ledger entry already participates in.

**[P2] (confidence: 5/10, medium — verify)** The hard-quota `unavailable` set is described
as "an in-memory set, not a config file" shared across `run_council`'s concurrent
`ThreadPoolExecutor` implementers. Python's GIL makes individual `set.add()`/membership
checks safe, but there's no stated ordering guarantee against a second in-flight dispatch
to the same now-excluded provider that started microseconds earlier — acceptable (matches
"never retried... mid-run" intent for anything dispatched *after* the mark), but worth a
one-line note in the implementation plan rather than leaving it implicit.

Everything else checked out against the real code: the 4000-char diff truncation
convention (`dispatch_code_fix.py:248`), the research-only vs write-trust runner split
(`notelab/research/evals.py` `KNOWN_RUNNERS` has no `--yolo`/`--auto`/`-a`, matching the
critique-call design), `could_not_run` honesty (`dispatch_code_fix.py:226`), `DEFAULT_TIMEOUT=420`
and kilo's ~123s figure (`dispatch_code_fix.py:50-55`), and the cross-family "Law 2" precedent
(`consensus_gate.sh:11`) are all accurately cited, not invented.

**OUTSIDE COVERAGE / CROSS-MODEL:** Not run — this was a spawned/background review session
with no live human available to drive the interactive Codex outside-voice gate or the
per-finding AskUserQuestion decision ledger this skill normally requires. Findings above
were verified by reading the actual source instead. Recommend a human or live session
confirm the P0 branch-naming fix and P1 provider-field design before implementation starts.

**UNRESOLVED DECISIONS:**
- Separator/field choice for the P0 fix (sanitized name vs. dedicated `provider` field on `RunnerSpec`).
- Whether to also set `correction_rounds`/`escalated_from_previous_step` per the P1 ledger finding, and exactly which hop transitions set them.
- `consensus_threshold` default (0.6) and the `judges` roster question — both already flagged as open questions in the spec itself, unchanged by this review.

## Revision after eng + devex review (2026-09-27)

Both `/plan-eng-review` and `/plan-devex-review` ran in parallel (background subagents,
per the user's request to use them as sparring partners, same pattern as the 2026-09-26
spec). As with that round, the two agents' file writes raced — only the eng review's
`## GSTACK REVIEW REPORT` section above survived on disk; the devex review's full write
(persona card, journey map, scorecard) was lost the same way. Its findings, delivered via
its handback report, are recorded here instead, alongside how every finding from both
reviews was resolved above:

**Eng review (3 findings, all fixed above):**
1. **[P0]** `RunnerSpec.name` examples (`"kilo:nemotron-ultra"`) contain literal colons —
   verified live to break `git check-ref-format`/`create_worktree`'s branch naming for
   every chain/council run. Fixed: git-safe hyphenated names + a new explicit
   `RunnerSpec.provider` field (default `""`, non-breaking for existing single-model
   entries) — "Expanded rosters" section.
2. **[P1]** No stated mechanism to recover "provider" from a composite name for the
   circuit-breaker/cross-family checks, and any name-splitting approach collides with
   real model ids that already contain colons. Fixed by the same `provider` field.
3. **[P1]** The ledger plan used only free-text `decision_reason`/`tags`, skipping the
   schema's existing structured `Attribution.correction_rounds` /
   `RoutingMetadata.escalated_from_previous_step` fields that dashboard/time-value views
   already read from every other entry. Fixed — "Ledger data" section now sets both.

**DevEx review (3 findings, all fixed above):**
1. No CLI surface was defined (Python signatures only, no flag/subcommand, no mapping
   onto the existing `--agents` convention). Fixed — new "Design — CLI surface and
   escalation reporting" section: `--pattern {race,chain,council}`, backward-compatible
   default.
2. Circuit breakers fired silently (no stdout/stderr line), inconsistent with the base
   script's loud-stderr house style; the only way to reconstruct "why did this escalate"
   would have been a raw SQLite query. Fixed — both breakers now print an explicit
   `[circuit-breaker]` line and are tagged in the ledger.
3. Escalation reporting was underspecified: no exit code, no file path for the
   chain/council-shaped report (round history / vote tally don't fit the existing
   per-agent `write_report()` format) — the same "report persistence" gap the
   2026-09-26 devex review already caught once, recurring for the new output shapes.
   Fixed — exit code `2` for "needs frontier review," `chain-report.{md,json}` /
   `council-report.{md,json}` under the existing `.swarm-runs/<task_slug>/` convention.

Both reviews' full findings (eng's complete GSTACK REVIEW REPORT above; devex's preserved
here) are the record of what was reviewed and why the design changed.
