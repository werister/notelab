# Evaluations

What is checked, how, what it showed, and how to rerun it. We report limits next to results.

## 1. Live guard evals: does a real agent get stopped?

Unit tests prove the guard's logic. This proves a live agent reasoning for itself is stopped, which is the
real threat model. Every scenario is read-only: `nlmx` refuses before `nlm` is called.

```bash
python -m notelab.research.guard_evals --notebook-id <id> --source-id <sid> --runners opencode claude
```

Scenarios: a `--source-ids` query (broken upstream: deselects every source), `chat start` (an unusable REPL for an
agent), and a research run with no notebook (must fail fast).

| runner | `--source-ids` refusal | `chat start` refusal | missing-notebook fail-fast |
|---|---|---|---|
| opencode | pass | pass | pass |
| claude | pass | pass | pass |

**6 / 6** across two model families, sweep of 2026-10-07.

Scoring is deliberately strict. Only substantive refusal markers count, so a bare command echo does not pass.
Runs that cannot produce a signal (monthly quota exhausted, usage limit, a shell-less spec blocked on approval)
are reported as environment errors, never as passes or failures.

## 2. Skill evolution: does a change earn promotion?

```bash
python skills/skill-evolution/scripts/evolve.py --battery skills/skill-evolution/eval/tasks/guard-battery.json --challenger v2
```

A challenger is promoted only if all four hold: it beats `max(margin, bootstrapped noise floor)`; its prompt growth
is paid for by measured gain; no held-out task regresses; the tree is green on both runs. Challengers whose text
contains suite secrets are rejected before any spend.

The recorded duels (`skills/skill-evolution/eval/tasks/guard-battery.history.jsonl`) both ended **HOLD**: the
challenger tied the champion (0.5 vs 0.5, then 1.0 vs 1.0), the margin was not beaten, and nothing was promoted.
That is the gate working as designed: an edit that doesn't measurably win doesn't ship. Limit: the battery is
small (three scenarios, two runners), so the noise floor is wide and only large wins can pass.

## 3. Research strategies: improving or regressing?

`research_eval_skill(strategy_name)` fits an OLS slope over a strategy's recorded scores and returns the raw
series alongside the verdict. The offline demo (`python examples/self_improving_loop.py`) shows
`improving (+0.081/run)` over `[0.34, 0.41, 0.49, 0.58, 0.66]`. Limit: the demo series is fixture data; real
trends need real runs.

## 4. Source scoring: auditable, deterministic

Scores are computed locally from the source list (domain, date, length, term overlap), with no model call, so the
same input gives the same score. Every composite decomposes into five signals and the weights are config
(presets: `news`, `lit_review`, `docs`, `balanced`). Run `research_score_notebook` on any notebook to see the
per-source breakdown. Limit: it scores the *sources a run retrieved*, not whether an answer is correct, and it
treats video domains and personal blogs as neutral authority.

## 5. Mixed-media research: what does one run find, and does it work?

For *"how transformers attention works: explainer videos, lectures, blog posts and the original papers"*, one fast
run returned 10 sources across 10 domains: two video lectures (3Blue1Brown, MIT 6.S191), three papers or course
notes (arXiv, ICLR, Stanford), blog walkthroughs and Wikipedia. Scored 0.79 overall, 0.89 diversity.

The check also found a defect in the raw pipeline: **both YouTube sources arrived as a bot-wall page**
("Sign in to confirm you're not a bot"), not a transcript. Adding the same video natively (`nlm source add
--youtube`) fetched about 28,000 characters of real transcript, and the notebook answered detailed questions about
each lecture from it. `research_fix_videos` repairs this and was verified live: plan flagged both, `apply` added
native copies, and a question about the MIT lecture was answered from its transcript. Limits: one topic, one
notebook, two videos; we do not know how often NotebookLM's importer hits the bot wall for other videos.

## 6. Code index

Offline tests (`tests/test_codeindex.py`) cover verbatim source for a definition, AST-accurate callers (a `def`
line is not a call), direct subclasses only, ranked text search, no-match handling, kept versions with
content-deduplicated storage, diffs (added / removed / changed functions), re-indexing the same version replaces
rather than duplicates, and non-Python symbol discovery. Used on real code: indexing click and Flask took about
half a second, and click 8.1.7 vs main diffed to 1 file added, 140 functions added, 78 removed, 293 changed.
Limit: Python gets AST precision; other languages use regexes and are approximate.

## 7. The whole package

```bash
scripts/verify.sh
```

The offline suite passes; the 2 optional/env-gated tests skip without keys. CI runs on Python 3.10 through 3.14.
`scripts/verify.sh` is the same checklist on your machine: imports, tests, the demo, the code index, both guard
refusals, and the skills' frontmatter.
