---
name: research-loop
description: "Self-improving deep research on Google NotebookLM via notelab's research MCP tools. Use when the task is to RESEARCH A QUESTION (not just operate NotebookLM): A/B-test research prompts on source-data quality, score or value-assess a notebook, evolve/curate its sources, spar model families, ingest a codebase as a source, learn what works per topic, or verify guards with live-agent evals. Triggers on: research loop, A/B test prompts, improve research, source quality, curate notebook, evolve notebook, spar models, ingest codebase, learn topic, guard evals, notelab."
version: "1.0.0"
---

# Research Loop (notelab)

You are operating the layer *above* the NotebookLM remote control: every
research run here is an experiment that gets better over time.

## The loop in 30 seconds

1. **Ask as an experiment** — `research_run_experiment(question, strategies)` runs
   2+ prompt variants into isolated notebooks and ranks them by *source-data
   quality* (authority, diversity, recency, depth, relevance). Read `winner`
   and `margin_over_runner_up`; never cherry-pick an arm by vibes.
2. **Curate the corpus** — `research_evolve_notebook(notebook_id, ...)` proposes
   KEEP/EVICT/MERGE/SKIP per source. It is a **dry run** unless `apply=true`.
   Read `actions` before ever applying.
3. **Remember what worked** — `research_learn_topic(question)` turns history into
   next-round mutations; `research_suggest_prompt(question)` replays past winners.
4. **Compare families** — `research_spar(question, prompt, families?)` runs one
   prompt through several model-family CLIs; `research_family_leaderboard()`
   ranks them. Missing CLIs are skipped, never failed.
5. **Verify guards live** — `research_adversarial_run(prompt)` drives installed
   agent CLIs at a prompt; the guard-eval battery (see cookbook) proves refusals.

Supporting tools: `research_score_notebook` (data quality now),
`research_assess` (fast/deep value verdict), `research_eval_skill` (is a
strategy improving over time?), `research_history` (past experiments),
`catalog_sync` / `catalog_search` (what do we already know?),
`research_ingest_codebase` (add a repo digest as a source).

## Rules

- **Scores are data quality, not answer quality.** A 0.9 arm found great
  sources; read them before claiming the answer.
- **Never pass `weights` to `research_evolve_notebook`** — it has no such
  param (it errors). Weights go to `research_run_experiment`,
  `research_score_notebook`, `research_assess`.
- **Evolve is dry-run-first, always.** Quote the plan, then ask before `apply=true`.
- **Guard work goes through `nlmx`, never bare `nlm`.** If a command refuses,
  report the refusal verbatim — do not retry around it.
- **Record, don't remember.** Wins live in `research_history` and the ledger,
  not in your context. Query them.

## Measuring improvement

Every loop above emits numbers (scores, margins, verdicts, PASS/FAIL). To turn
a one-off win into a durable gain, run it as a battery and gate promotion on
the numbers — see the `skill-evolution` skill for the champion/challenger
protocol, and `references/loop-cookbook.md` for three worked flows.
