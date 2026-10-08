# Loop cookbook — three worked flows

Each flow names the exact tool sequence. Replace the placeholders; keep the order.

## 1. Improve a research prompt (the core loop)

Goal: find the best way to ask NotebookLM about X.

1. `catalog_search(query=X)` — what do we already know? If a prior experiment
   covers X, start from its winner (`research_history`).
2. `research_suggest_prompt(question=X)` — replay a past winner if one exists.
3. `research_run_experiment(question=X, strategies=[{name, prompt} × 2-3])` —
   keep prompts differing in ONE dimension (specificity, source-type hint,
   discipline framing). Record the experiment id.
4. Read `winner` + `margin_over_runner_up`. Margin < 0.05 → tie: keep both,
   do not overclaim.
5. `research_learn_topic(question=X)` — bank the mutations for next round.

## 2. Curate a notebook (prune + feed)

Goal: raise a notebook's source quality over time.

1. `research_assess(notebook_id, question=X, mode="fast")` — baseline verdict.
2. `research_evolve_notebook(notebook_id, question=X)` — read the dry-run plan
   (KEEP/EVICT/MERGE/SKIP + reasons). Quote it to the user.
3. Only on approval: same call with `apply=true`.
4. Feed: `research_ingest_codebase(repo)` for repos, or a fresh
   `research_run_experiment` arm for new questions.
5. Re-assess. Score delta is the improvement receipt.

## 3. Verify a guard (prove the refusal)

Goal: prove a live agent gets stopped — the eval mindset in miniature.

1. Pick the scenario (source-ids refusal / chat refusal / fail-fast).
2. `research_adversarial_run(prompt=<the exact scenario prompt>, runners=[...])` —
   use 2+ families when credit allows; one is a smoke test, not proof.
3. Score transcripts against the refusal markers (see `skill-evolution` for
   the honest-scoring rules: substantive markers only, env failures are
   no-signal, never pass/fail).
4. A PASS from a capable runner is signal. A FAIL from a runner that can't
   execute tools (no shell, quota dead) is a runner finding, not a guard finding.
