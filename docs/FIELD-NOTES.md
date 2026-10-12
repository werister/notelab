# Field notes — nlmx / the research server in heavy use (2026-09-20)

Observations from driving `nlmx` across **5 back-to-back deep-research passes** (~145 sources
imported into one notebook) to build an external evidence base. These are reproducible frictions
and small robustness gaps, mapped to this repo's components, so they can become fixes. None of
the five gaps below are fixed yet as of 2026-09-24 — this doc is the backlog, not a changelog.

## What worked well
- **`research start --mode deep` → `research status` → `research import`** is reliable: 5/5 passes
  completed (~5 min each, 31–56 sources) and imported cleanly. The idempotent `research run` guard
  (`nlmx_adapter.research_run`) is the right call — no duplicate-import corruption observed.
- **`notebook query` returns structured JSON** (`.answer`, `.citations`, `.sources_used`) — excellent
  for programmatic synthesis; citations map to source ids so claims stay traceable.
- The notebook accumulated 5 passes' sources without degradation; cross-pass queries synthesized fine.

## Observed inefficiencies / robustness gaps (candidate fixes)

### 1. `research status` blocks and can exceed the client timeout — FIXED 2026-09-24
`nlmx research status <nb>` **waits for completion** (spinner) rather than returning current state,
so a poll can run 40–90s and a short client timeout returns `rc=124` that *looks* like failure when
research is merely still running.
- **Fix idea:** a `--no-wait` / `--poll-once` flag that returns `{status, sources_found}` immediately;
  or document that `status` blocks and the adapter should use a long timeout (the adapter's 900s default
  is right, but ad-hoc CLI use trips on the shorter default).
- **Reproduced concretely:** ran a real `research_run_experiment` (2 arms, deep mode, a genuine
  two-topic question) through the MCP server. Both arms actually completed server-side — 54 and 42
  sources — but `nlmx research run`'s hardcoded `max_wait="600"` for deep mode gave up first, so
  `research_run_experiment` reported `error: ... did not complete within 600s` with a 0-source,
  0-score result even though the research had genuinely succeeded. Recovered manually
  (`nlm research status --max-wait 5` confirmed `completed`, then `nlmx research import`).
- **Fix applied:** bumped `nlmx`'s deep-mode `max_wait` 600s → 1200s, and `NlmxAdapter`'s outer
  subprocess timeout 900s → 1500s to match (must stay above nlmx's internal wait + import-step room).
  Still a fixed constant, not adaptive — a `--no-wait`/streaming-status API on the `nlm` side would be
  the real fix; this just moves the wall further out based on one real data point.

### 2. No live model-id resolution → stale ids 404 silently as "no access"
Calling providers with a guessed model id (`gemini-2.5-flash`) returned
`404 "no longer available to new users"`, and on some providers a wrong id surfaces as a misleading
`402/no-access`. The reliable path was always `GET /v1/models` first.
- **Fix idea:** the adapter/skill should resolve the current served id from `/v1/models` (or expose a
  `nlmx source models`-style helper) before a call, and treat "model not found" distinctly from auth/quota.

### 3. Reasoning models return `content:null` unless `max_tokens` is generous
Several free/reasoning models (OpenRouter `:free` reasoning ids, Gemini flash, Cerebras qwen/gpt-oss)
returned `finish_reason:"length"` with the answer stuck in a `reasoning` field and `content:null` when
`max_tokens` was small (≤256). Raising to ≥800–2000 fixed it.
- **Fix idea:** when a response has empty `content` but a non-empty `reasoning` field and
  `finish_reason=="length"`, the adapter should surface that as a distinct "truncated-reasoning" outcome
  (and optionally auto-retry with a higher cap) rather than recording an empty result.

### 4. `import` reports fewer sources than `status` found
`status` reported e.g. 56/49/48/40 "found" but `import` added 49/39/27/30 — the delta (dedup / failed
fetches) is silent.
- **Fix idea:** have `import` print the skipped count + reason (duplicate / unfetchable), so the
  `research/evolve.py` create/merge/skip accounting reflects reality.

### 5. `notebook create` title is positional, not `--title`
Minor DX: `--title` is rejected (`No such option`). The adapter uses the positional form correctly;
worth a one-line note in the skill so callers don't hit it.

### 6. `nlmx_adapter.py` had drifted from the real installed `nlm` CLI surface — FIXED 2026-09-24
Found running the same live experiment above (before it even reached the timeout in gap #1):
`create_notebook` passed `--quiet` to `nlm notebook create`, which has no such flag in the
installed `nlm` 0.6.5 — the call failed instantly with `No such option: --quiet`. Separately,
`remove_source` called `nlm source remove <notebook_id> <source_id> --confirm`; the real
subcommand is `delete`, not `remove`, and `source delete` takes no notebook_id argument at all
(it failed safe — non-zero exit, no corruption — but the evict path in
`research_evolve_notebook` was completely non-functional). Neither was ever exercised
against the real CLI: **every offline test mocks `NlmxAdapter` entirely** (`tests/test_integration.py`'s
`FakeAdapter`), so 126 passing tests gave no signal either was broken. Fixed both, plus a related
id-parsing bug (`create_notebook` took the raw last stdout line as the id; without `--quiet`,
`nlm notebook create` prints two lines, so the "id" would have come back as `"ID: <uuid>"` with
the prefix still attached) — verified all three against the real CLI (create → add a source →
delete it → delete the notebook, nothing left behind).
- **Takeaway:** an offline-only test suite, however thorough, cannot catch CLI-surface drift.
  Worth a `RUN_LIVE_NLM_TESTS=1`-gated smoke test that exercises `NlmxAdapter` against the real
  `nlm` binary (create/list/delete a throwaway notebook) — not run in CI, but runnable on demand
  before trusting the adapter after an `nlm` upgrade.

### 7. `nlmx research import` can report failure via exit code after actually succeeding
Same live run: the second arm's import printed `Error: Failed to import sources: The read
operation timed out` (nonzero exit) — but `nlmx`'s own before/after source count showed
`0 -> 41`, i.e. the import had genuinely landed server-side; only `nlm`'s own HTTP client read
timed out waiting for the confirmation response. `nlmx`'s ledger already writes the correct
`count_after` regardless of the propagated exit code (so the *duplicate-import guard* stays
correct), but a caller watching only the exit code — like `research_run_experiment` — would
wrongly treat a successful import as failed. Not fixed here: this is `nlm`'s own read-timeout
behavior on a large batch response, not something `nlmx` caused; a real fix means `nlmx`
re-checking actual source count after a reported import error before deciding to propagate
failure, which is more than a one-line change and wasn't warranted for one observed instance.

### 8. `research_assess(mode="deep")`'s ablation epsilon is only usable at N=2 sources — FIXED 2026-09-24
Leave-one-out ablation (`ablate_sources`) classifies a source "redundant" when removing it
drops the composite by less than `composite_eps=0.02` (2%) and loses no question-coverage.
The composite is a plain per-source **mean** across N sources, so removing one shifts each
signal's mean by roughly `(mean − that source's value) / (N−1)` — structurally shrinking as N
grows, independent of how different that source actually is from the rest.

Confirmed live: ran `research_assess(mode="deep")` on a real 41-source notebook — **all 41
sources came back "redundant"**, composite deltas all under 0.005, despite the notebook's own
`research_score_notebook` diversity score (0.49) and wildly varying per-source relevance
(0.02–0.79) showing real heterogeneity. Reproduced offline with synthetic data at several N,
using a deliberately extreme 10x content-strength gap between "strong" and "weak" sources so
the ablation had something obvious to find:

| N | verdicts | composite_delta range |
|---|---|---|
| 2 | both `marginal` (correctly distinguished) | 0.17 – 0.23 |
| 3 | all `redundant` | -0.019 – 0.009 |
| 4 | all `redundant` | -0.009 – 0.009 |
| 6 | all `redundant` | -0.006 – 0.006 |
| 41 (real data) | all `redundant` | -0.005 – 0.005 |

The fixed 0.02 threshold only clears the bar at N=2. By N=3, even a 10x-stronger source can't
push its own removal past a static 2% composite move, because one-of-three is inherently ~⅓
of the mean's weight at most and shrinks further with N. This isn't a narrow edge case — it's
the common case for any real notebook (most hold well over 3 sources).

**Fix applied**: rather than the k/N-scaling or percentile-ranking ideas above (both still
just a different arbitrary threshold), added an opt-in Laya-based tie-breaker
(`research/laya_judge.py`, `use_laya=True`) for exactly the ambiguous zone this bug exposed —
sources with a clear signal never touch it, only the ones the fixed epsilon can't
distinguish. The design was reviewed before implementation; review caught real gaps
(a verdict-string bug and a missing wiring path), fixed before merge.

Getting Laya's own question design right took real iteration, not a first-try success — worth
recording since it's the same "verify, don't assume" discipline this whole document is about.
The first design asked a direct binary `choice` ("is this redundant or load-bearing, given what
the rest of the set covers") — live-tested, it came back a near coin-flip (52/48, confidence
<0.02) regardless of how differentiated the sources actually were, across three different
fixture attempts. Redesigned to a `score`-type question asking Laya to rate each source's own
specificity (concrete facts vs. vague restatement) independently, with a threshold on the
result — same checkpoint, same fixture, this shape gave a clean, confident, correct signal
(a source with real names/numbers/policy details scored ~2.0/3 "mostly specific"; generic
keyword-restatement filler scored ~0.6 "vague").

**Verified live** on the real 41-source notebook this gap was originally diagnosed on:
`research_assess(mode="deep", use_laya=true)` took 128.4s (all 41 sources landed in the
ambiguous zone — Laya was consulted for every one), and returned **29 load-bearing / 12
redundant** — a real, differentiated split, not the original all-41-`redundant` result, and not
a suspicious all-load-bearing rubber stamp either.

### 9. `research_spar`'s "model families" never actually run through their named CLI — FIXED 2026-09-30
The module docstring and tool description ("run one prompt across model families... which
family finds the best sources") strongly implies each family's own AI backend does the
research. It doesn't: `ToolLogic.spar`'s `family_runner` calls `detect_runners()` only to
decide whether a family is *allowed to participate* (its CLI binary is on PATH), then routes
every family's actual research through the exact same path everything else uses —
`self.adapter.research_run(Strategy(name=f"spar-{family}", prompt=p))` — pure NotebookLM deep
research, identical for every family, with the family name being cosmetic notebook-title
labeling only. Confirmed by grep: zero references to `family` anywhere in `nlmx_adapter.py` or
`research/ab.py`. So `research_family_leaderboard`'s "which family finds the best sources"
ranking isn't comparing model families at all — it's comparing repeated NotebookLM runs of the
identical prompt, mislabeled by whichever CLI happens to be installed. Separately,
`Strategy.mode` defaults to `"deep"` and `research_spar`'s schema exposes no `mode` parameter,
so every sparring session is hardcoded to the most expensive research mode with no fast-mode
option. Not fixed here — routing each family through its own actual CLI (rather than NotebookLM)
would be a real feature build, not a bug fix; flagging so the "leaderboard" isn't trusted as
real model-family comparison until it is.

**Fix applied**: `ToolLogic.spar()`'s `family_runner` now calls `run_adversary(spec, p, ...)`
per family — each family's research genuinely goes through its own CLI, not a shared
NotebookLM path. Confirmed via `grep -n "run_adversary" src/notelab/_tools.py`. The
`Strategy.mode` / no fast-mode-for-spar limitation noted in the original paragraph is
still real and still unfixed.

### 10. Blind third-party validation (qoder, zero prior context) — `--help` bug FIXED, three real gaps documented
Per request: gave `qoder` (a coding-agent CLI with zero context on this repo or conversation) a
fresh `git clone` and one instruction — get it running, use its own tooling to research a real
question ("FastAPI vs. Flask for a small team in 2026"), report what happened. This is the
closest thing to the PR #6 "would a stranger's agent actually be able to use this repo"
question this repo has had, answered by an actual stranger rather than a self-check.

**The core pipeline genuinely works for a stranger**: it self-onboarded (independently
rediscovered the `uv` workaround for the broken system-Python `pyexpat` issue), correctly used
`research_run_experiment` with sensibly-named strategies, got a real synthesized answer, and
persisted a real ledger entry (experiment #5). That's a substantive, independently-earned
result, not just "it ran."

**Fixed**: `nlm-research-mcp --help` printed nothing and silently started the server (hung on
stdin) instead of showing usage — `server.py::main()` only ever checked for `--sdk`, any other
arg fell through to `build_stdio_server().serve_forever()`. Added a `--help`/`-h` check with a
real usage message, tested (`tests/test_server_cli.py`).

**Not fixed, documented**:
- `catalog_search` returned unrelated existing-account results for "FastAPI Flask Python API" —
  plausibly the OR+prefix-match recall fix in gap #6/storage.py trading precision for recall on
  short, common English terms; not confirmed root-caused, flagging for follow-up rather than
  guessing at a fix.
- Authority scoring gave Wikipedia **0.95** and the *official* FastAPI/Flask documentation only
  **0.5** in this real run — the winning arm's 45-source corpus included unrelated AI docs, a
  corporate PDF, and CVE material despite explicit anti-SEO instructions in the prompt, and
  `quick_assess` correctly flagged it "bloated" but `research_run_experiment` still declared it
  the winner on raw composite score. `quality.py`'s authority heuristic doesn't recognize a
  question's own official-docs domain as higher-authority than a general encyclopedia — a real
  scoring-quality gap, not a bug with an obvious one-line fix (same class of "needs real
  calibration" as gap #8 was before the Laya fix).
- Real onboarding friction, independent of the doc fixes already shipped (PR #6 etc.): multiple
  external prerequisites (a working Python, `pipx`/`nlm`, browser auth, `nlmx` on PATH, an MCP
  client) still have to be assembled by hand; no single skill/script captures "get this whole
  stack running" in one step. qoder's own recommendation: a project-level environment-setup
  skill, plus source-set relevance filtering before ranking rather than after.

### 11. Second blind review round (Kiro, fresh clone, post-merge) — two real `nlmx` bugs FIXED
Ran a second independent blind review after gap #10's round of merges — `kiro-cli`, zero prior
context, fresh clone, asked for a genuine code review plus a real research task on a topic
unrelated to anything tried before (CRDT vs. operational-transform sync for a 2026 collaborative
editor, to check research quality on a fresh domain). In parallel, gave `qoder` a second, distinct
task focused specifically on the newly-merged Laya feature.

**Fixed, both in `nlmx` itself:**
- `NLM` (the path to the real `nlm` binary) was hardcoded to `~/.local/bin/nlm` with no
  fallback — `NlmxAdapter` (the Python side) already resolves its own binary via `NLMX_BIN` env
  / `PATH` / `~/.local/bin`, but `nlmx` never applied the same logic to the `nlm` binary it
  wraps. A `pipx`/`uv tool install` landing `nlm` somewhere else would make every `nlmx` call
  silently target a path with nothing there. Fixed with the same resolution order the adapter
  already uses (`NLM_BIN` env → `PATH` → `~/.local/bin` fallback).
- `cmd_research_import` wrote a "successfully imported" ledger entry **unconditionally**,
  regardless of whether the underlying `nlm research import` call actually succeeded. A genuine
  failure (network error, invalid task, zero sources added) still got recorded as done — the
  *next* legitimate retry would hit the "already imported, pass --force" guard and die(), even
  though nothing had actually landed. Fixed: only write the ledger entry when the exit code
  says success **or** the source count actually grew (the second condition matters because of
  gap #7 — a *real* success can still report a nonzero exit on an HTTP read timeout after the
  data already landed, so exit code alone isn't trustworthy in either direction).

**Design observations, not fixed** (Kiro's review, real but lower-priority):
`evolve.py`'s sole-cover coverage guard emits a KEEP plan item for a source without adding it
to `survivors`, making it invisible to budget-ranking if `keep_budget` is tight; `storage.py`'s
FTS tokenizer migration check re-queries `sqlite_master` on every `Store()` construction (cheap,
but a "migration done" flag would avoid it entirely); `_STOPWORDS` doesn't exclude
coverage-irrelevant words like "more"/"approaches", so `quick_assess`'s coverage-gap list can
include meaningless terms. None of these are wrong enough to block anything — flagged for a
future pass.

**Positive, worth recording**: Kiro's unprompted assessment called out the `plan_evolution`
(pure) / `apply_plan` (confirm-gated) separation, the injected-fake testability pattern, the FTS
fix's inline rationale, and the Laya singleton + pinned-revision pattern as "exemplary" /
"exactly right" — independent confirmation that the design decisions from earlier gaps in this
document hold up under a fresh, uninstructed read, not just self-assessment.

### 12. Back-to-back `nlmx research run` on the same notebook silently duplicates the FIRST
query's sources instead of running the SECOND — NOT FIXED
Hit live while seeding a fresh research notebook (2026-09-24): ran
`nlmx research run "<query 1>" --notebook-id <nb> --mode deep --source web`, which completed
normally and imported 65 new sources (21 → 86). Immediately ran a **second**
`nlmx research run "<query 2>" --notebook-id <nb> --mode deep --source web` against the **same**
notebook. The internal `nlm research start` call detected a prior research task NotebookLM still
considered pending/unconsumed and printed an interactive conflict prompt:
```
Warning: Previous research completed with sources not yet imported.
  Task ID: <TASK_ID>
  Sources available: 66
Starting new research will discard existing results.
Continue and start new research? [y/N]:
```
Piped `y` in non-interactively (reasonable, since the notebook's source count already showed the
prior batch had in fact been imported — 86, matching 21+65 exactly). Confirming did **not** just
discard stale state and move on: it (or the subsequent status/import chain) **re-imported query
1's exact source list a second time** — 65 exact duplicate titles/URLs, notebook count 86 → 151 —
while query 2 ("detecting bugs/regressions in AI-agent code changes...") **never actually ran**:
the polled `nlm research status --task-id <query-2's-token> --max-wait 1200` returned
`Status: completed` in well under a second (impossible for a real ~5 min deep pass) and reported
the **same** `Task ID: <redacted>` / `Sources found: 66` as query 1's status check, not a status
for query 2 at all. `nlmx` had no way to detect this — it trusted "completed" and imported, so the
caller sees a clean `✓ Research started / Query: <query 2 text>` banner with no error, but the
notebook only ever received duplicates of query 1.

**Root cause (best evidence, not confirmed against nlm's server internals):** `nlm research
start`'s printed `Task ID:` is an opaque RPC continuation token (e.g.
`ChBmYWtlLWV4YW1wbGUtdG9rZW6PAQAaATMqA3Vzcg==`); `nlm research status`/`research import` report and key
off a *different*, human-readable UUID-style task id (e.g. `00000000-0000-...`). These two id
spaces are never reconciled anywhere in the CLI output, so `nlmx`'s
`re.search(r"Task ID:\s*(\S+)", ...)` — which extracts the opaque token from `start`'s stdout and
passes it straight to `status --task-id` / `import <task_id>` — is passing an id those commands
don't actually recognize. NotebookLM appears to track only **one current research slot per
notebook**; `status`/`import` without a *matching* id silently fall back to reporting/importing
that one slot's most recent state rather than erroring on an unrecognized id, which is what makes
this silent instead of a clean failure.

**Consequence:** any workflow that calls `nlmx research run` more than once against the same
notebook without a real wait in between (a natural pattern — asking a notebook several distinct
research questions in sequence, exactly what this session was doing) risks silently duplicating
the first query's sources instead of running later queries, with a misleading success banner and
no error signal. Recovery required manually diffing `nlm source list --json` by URL, then deleting
duplicates.

**Also found in the same session, same recovery**: `nlm source delete <id1> <id2> ... --confirm`
fails **silently** (`Error: Failed to delete source(s).`, no further detail, exit reflects failure)
for anything beyond a single id — verified single-id delete succeeds 100% of the time, an 8-id
batch fails 100% of the time. No batch size was found that works; recovery required looping
one-id-at-a-time deletes (64 individual calls, all succeeded, just slow).

**Fix directions (documented, not implemented):**
1. Before starting new research on a notebook, `nlmx` should proactively `research import` any
   pending prior task itself (governed by its own ledger, like `cmd_research_import` already does
   for the normal case) rather than piping a bare `y` through `nlm`'s interactive prompt, whose
   side effects `nlmx` doesn't fully control.
2. `nlmx`/`nlm` need one consistent task-id representation across `start` → `status` → `import`,
   or `nlmx` should re-resolve the real id via a `status --max-wait 0` call immediately after
   `start` rather than trusting the id `start` prints.
3. `nlm source delete` bulk mode needs a workaround (delete one id per call) until root-caused
   upstream — silent failure with no diagnostic on anything past a single id is a trap for any
   caller that assumes the documented `<id1> <id2> <id3>` bulk form works.

**Companion finding, same session**: once the notebook above had grown to ~218 sources (the 21
seed sources plus 4 deep-research imports), `nlm query notebook` (the chat/synthesis endpoint,
separate from `research start`/`import`) became unreliable — `Connection reset by peer` and read
timeouts, including on a **trivial, non-grounded** control query ("say hello in one word"),
ruling out "the real question is too expensive to answer" as the sole explanation. Scoping every
query to a small, topic-relevant set of source ids via `--source-ids` (4–6 ids instead of all 218)
was reliably successful — first or second attempt every time, vs. 3–5 failures per unscoped
attempt on the same notebook. Not root-caused (client-side timeout vs. a real server-side limit
on large-notebook chat context is unconfirmed either way), but the workaround is simple and
worth documenting: **once a notebook accumulates 100+ sources from repeated `research run` calls,
prefer `--source-ids`-scoped queries over whole-notebook queries.**

### 13. Guard-eval FAILs from tiny local models mean "can't follow the protocol", not "guard broken" — 2026-10-07

The new `research/guard_evals.py` harness was proven live two ways. opencode (nemotron-3.5,
with tool use) ran the forbidden `nlmx chat start` exactly as instructed, got refused, and
the harness scored PASS with 3/3 refusal markers — the guard and the scorer both work
end-to-end. The same sweep on ollama `llama3.2:1b` FAILed both scenarios: a 1b model with
no tool use just generates text about the command instead of running it, so no refusal
marker can ever appear. Lesson for reading future reports: a FAIL from a *capable* runner
is the real guard-broken signal; a FAIL from a runner that can't execute tools is a
runner-capability finding. Same session also capped stored transcripts at 4000 chars
(markers are still searched in the full text) after agent-CLI output ran to tens of KB
per scenario.

### 14. `research_ingest_codebase` live validation + empty-input guards — 2026-10-07

The new ingest tool was validated live against a scratch notebook: fixture repo →
auto-created `[codebase]` notebook → `source add --text` landed with the right title →
verified via ground-truth `source list` → notebook deleted. Round trip works. Review of
the same path found two fail-open edges, both fixed: a nonexistent local path (or a file
where a directory was expected) used to produce a 0-file digest that would have been
added as a ~200-char junk source — `ingest()` now raises `ValueError`, and the tool
refuses to add 0-file digests with a "nothing added" error instead.

### 15. Guard-eval false PASSes from prompt-echoed markers — FIXED 2026-10-07

The first live guard sweep (opencode/copilot/claude × 3 scenarios) surfaced a
scoring bug, not a guard bug: the refusal-marker lists included the bare string
"nlmx", which appears in every scenario prompt — so a transcript that merely
echoed the command scored PASS. Two false PASSes on quota-dead Codex runs
proved it. Fixed three ways: (1) markers are now substantive refusal phrases
only; (2) a no-marker transcript is checked against environment-failure
fingerprints (quota/usage-limit/approval-block/auth) and reported as
`error`/no-signal instead of FAIL; (3) guard runs use an execution-capable
claude spec (`--allowedTools Bash`) since the shared research spec is
deliberately shell-less and produced approval-blocked no-signal runs. Re-scored
matrix: opencode 3/3, claude 3/3, everything else an honest environment error.

### 16. Eval sweeps wedged opencode's shared background service — FIXED 2026-10-08

Bare `opencode run` attaches to a shared background service, not a private
server — and every unanswered permission prompt from a headless eval run piled
into that service's in-memory approval queue (capacity 32; the sqlite
`permission`/`session_pending` tables stay empty, so there is nothing to
delete — only a service restart drains it). Our duels filled it: duel #2 came
back opencode 0/3 with three identical `Transport: The socket connection was
closed unexpectedly` failures, and interactive use failed with `model failed:
pending approval state is full: capacity 32`. Fixed three ways: (1) the
opencode runner spec now passes `--standalone` (private server per run —
hermetic evals with no trust change, unlike `--auto`); (2) both failure
fingerprints are scorer no-signal, not FAIL (duel #2's 0.5/0.5 was a scoring
artifact — claude was 3/3 throughout); (3) duels now dump per-run transcripts
to a temp evidence JSON, so the next runner failure is diagnosable without a
re-probe. Lesson: eval runners must be hermetic against shared daemon state,
not just against each other.

Addendum, same day: `--standalone` is correct but not universal — the private
server must bind a localhost port, which sandboxed runners block, so duel #4
came back opencode 0/3 with `Standalone server exited before reporting
readiness` on all six runs (claude 3/3 throughout). That crash is now a
no-signal fingerprint too: in sandboxes opencode runs abstain honestly and
duels there exclude it via `--runners`, while open environments (user
terminal, CI) get the hermetic default. The evidence-dump added for this
exact situation diagnosed duel #4 in one read, no re-probe needed.

## Why this matters for the repo's own thesis
This repo's pitch is *comparing source DATA QUALITY and A/B-testing prompts for deep research*
(`docs/DESIGN.md`). Two of the gaps above (2 and 3) directly affect that: a stale-id 404 or a
`content:null` truncation would silently poison an A/B comparison (`research/ab.py`) or a
`quality.py` score with a false "empty/failed" datapoint. Distinguishing *truncated-reasoning* and
*stale-id* from a genuine low-quality source keeps the self-improving loop honest — the same
"verify, don't assume" discipline the engine already applies to sources.

## Provenance
Notebook `Agent Routing and Self-Improvement Evidence Base` (id redacted), 5 deep passes on model
routing / self-improvement / cost-monitoring / decision-gate literature; findings live in the
external `werister/agent-routing-evidence` repo. These notes are the *tooling* takeaways, not the
research content.

Originally written against `werister/nlm-agent-guard` (the predecessor of this repo, PR #2,
2026-09-20) and rescued here on 2026-09-24 when that repo's outstanding work was consolidated
into `notelab` — the code paths referenced above (`nlmx_adapter`, `research/ab.py`,
`research/evolve.py`, `research/quality.py`) are this repo's, not the old package name.
