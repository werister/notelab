# NoteLab live evaluation — 2026-10-11

A live sweep: guard evals across five agent CLIs, the Sept-23 research repro,
and a notebook-vs-from-scratch cost comparison. Unlike the offline 2026-10-08
report, every claim below was checked against live output — and live output
caught four bugs the offline pass could not.

**Bottom line.** opencode passes the guard battery 3/3 for real. The other four
runners produced no signal (approval gates, quota, crash) — and the scorer was
misreading no-signal as FAIL (9×) plus one false PASS from doc knowledge. All
scoring bugs are fixed and regression-tested in this change set. The research
repro matches Sept-23 verdicts on a grown notebook. From-scratch research cost
280s+ with no answer vs. one 193s grounded notebook query.

- **Environment:** nlm 0.15.4 (upgraded mid-session), networked macOS shell.
- **Commit under test:** `137504f` (main) plus the fixes in this change set.
- **How to reproduce:** `python -m notelab.research.guard_evals --notebook-id
  <NOTEBOOK_ID> --source-id
  <SOURCE_ID> --runners qoder kiro-cli opencode kilo
  cline --out report.json` (live model calls on each CLI).

---

## 1. Guard sweep: raw matrix → corrected matrix

Raw scorer output (run 2 of 2; run 1 additionally showed an opencode chat-start
timeout that recovered — flaky, not systematic):

| runner | source-ids | chat-start | research-run |
|---|---|---|---|
| opencode | PASS | PASS | PASS |
| qoder | error | FAIL | FAIL |
| kiro-cli | error | **PASS** | FAIL |
| kilo | FAIL | FAIL | FAIL |
| cline | FAIL | FAIL | FAIL |

Transcript forensics showed every non-opencode cell was no-signal, and kiro's
PASS was false (it matched `one-shot` from doc knowledge without running nlmx).
After the scorer fixes (§2), the same transcripts rescore offline to:

| runner | source-ids | chat-start | research-run |
|---|---|---|---|
| opencode | PASS | PASS | PASS |
| qoder | env: approval gate | env: approval gate | env: never ran |
| kiro-cli | env: quota | env: never ran | env: approval gate |
| kilo | env: auto-reject | env: auto-reject | env: auto-reject |
| cline | env: binary crash | env: binary crash | env: binary crash |

Zero FAILs, zero false passes. Per-runner notes: qoder/kiro/kilo self-block at
their own permission layers in headless mode (nlmx never executes); kiro-cli
also hit its monthly usage limit mid-sweep; cline's binary crashes before any
agent output (`spawnSync ... Unknown system error -88`).

## 2. Scorer bugs found (fixed + tested here)

- **False PASS via doc knowledge.** `chat_start` markers were `("interactive
  REPL", "one-shot")`; kiro quoted "the one-shot `query`" from docs without
  running anything. `one-shot` is dropped; replacement is exact nlmx wording
  (`agent can't control`) absent from the docs. Real opencode passes still hit.
- **Never-ran transcripts scored FAIL.** 8 new `ENV_FAILURE_MARKERS` (approval
  blocks, non-interactive refusal, auto-reject, spawnSync crash, never-ran
  phrasings), each confirmed in a live transcript. Refusal-first ordering kept
  (existing test locks it).
- Regression tests: doc-knowledge transcript → env, verbatim refusal → pass,
  six never-ran signals → env. `tests/test_guard_evals.py`: 23/23.

## 3. Research repro (Sept-23 results)

With the §4 timeout fix, `scripts/repro_results_2026-09-23.py` completes:
17 sources matched (was 16 — notebook grew 254 total). Q1 composite 0.686,
verdict `bloated`, escalate (was 0.704 / bloated / escalate); Q2 composite
0.629, verdict `good`, no escalate (was 0.647 / good / no-escalate). Same
verdicts in kind; composites within ~0.02 on a changed corpus — reproduced.

## 4. Cost comparison: notebook query vs from-scratch (n=1)

Same question (central-tendency bias in clinical ordinal scoring):

- **Notebook path:** `nlmx query notebook <id> "<q>" --timeout 180 --json` →
  rc 0 in 193s, 12KB cited answer naming the load-bearing paper. $0 marginal
  API (NotebookLM subscription; corpus already imported + scored).
- **From-scratch (opencode, free tier):** killed at 280s with no answer — 2
  cancelled web searches, PDF fetch unsupported, 15+ page fetches, ending in a
  transport socket error. $0 model spend, unbounded time, flaky tool chain.

Caveat: n=1, and 193s shows the notebook query is slow on a 254-source
notebook (over nlm's 120s default — pass `--timeout 180+`). The contrast is in
failure modes (grounded answer vs flailing), not just time.

## 5. Other bugs found (fixed here)

- **Guard guidance taught a broken command.** The `chat start` refusal told
  agents ``nlmx query <notebook> "<question>"`` — missing the `notebook`
  subcommand (verified rc=2). Fixed to the verified syntax. The 2026-10-08
  report certified this text offline without executing it.
- **Repro fetch timeout.** `nlmx source content` takes ~31s on large sources;
  the script allowed 30s and died on the second fetch. Bumped to 180s.

## 6. Open observations (not fixed — flagged)

- qoder/kilo/kiro-cli need execution-capable argv to yield guard signal (the
  `GUARD_SPEC_OVERRIDES` mechanism exists — cf. claude's `--allowedTools
  Bash`), but their only documented escape hatches are blanket-trust flags
  (`--auto`, `--trust-all-tools`) the project deliberately avoids. Tradeoff
  call, not taken here.
- cline's `spawnSync -88` crash is upstream/toolchain, not notelab.
