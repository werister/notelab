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

### 8. `research_assess(mode="deep")`'s ablation epsilon is only usable at N=2 sources
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
the common case for any real notebook (most hold well over 3 sources). Not fixed here: the
right fix is a genuine design call (scale `composite_eps` by something like `k/N`, or switch to
a relative/percentile ranking of deltas across the source set rather than a fixed absolute
threshold), not a one-line patch, and needs calibration against real data at several N rather
than a guess.

### 9. `research_spar`'s "model families" never actually run through their named CLI
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
