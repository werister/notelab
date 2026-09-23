# nlm-research-mcp — Design

A **self-improving research MCP server** built on top of Google NotebookLM.

Most "NotebookLM MCP" integrations stop at *plumbing*: they expose `create notebook`,
`add source`, `query`. That is a remote control, not a research system. `nlm` (upstream)
already does that well, and `nlmx` (this repo) guards its three confirmed corruption bugs.

This server adds the layer that is actually missing: **it treats deep research as an
experiment that gets better every time you run it.** It runs research, *measures the
quality of what came back*, A/B-tests the prompt that produced it, and keeps the winners.
Over time your research prompts stop being guesses and start being tuned instruments —
per topic, per model family, per source mix.

That is the thing people want to run all the time.

## The one-sentence pitch

> An indexed, self-tuning research memory for agents — NotebookLM as the retrieval/synthesis
> engine, this server as the scientist that decides *which way of asking* actually produces
> good sources, and remembers.

## Why NotebookLM as the substrate (vs. a vector DB / Obsidian)

- **It holds real research data, not just your notes.** NotebookLM ingests web, YouTube,
  Drive, PDFs and grounds answers *in those sources with citations*. An Obsidian vault holds
  what you already wrote; this holds what the world knows, indexed and queryable.
- **Grounded synthesis is built in.** We don't have to build RAG — we get cited answers.
- **Deep Research is a first-class op.** `nlm research start/status/import` runs an actual
  multi-source research task. That is the raw material our loop optimizes.

The server sits *above* NotebookLM and owns the parts NotebookLM does not: a local **catalog**
(the Obsidian-like index of what's in which notebook), an **experiment ledger** (what we tried
and what scored well), and **session/attribution** so several model families can spar over one
shared corpus without stepping on each other.

## Architecture (layers, built bottom-up)

```
┌──────────────────────────────────────────────────────────────┐
│  MCP tools  (FastMCP)   ← what agents call                    │
│    research.run_experiment · research.compare · quality.score │
│    catalog.search · catalog.sync · notebook.* (guarded)       │
├──────────────────────────────────────────────────────────────┤
│  Self-improving research engine                               │
│    quality.py   — score the SOURCES a research run returned    │
│    ab.py        — prompt A/B: run N variants, rank by quality  │
│    strategy.py  — remember winners; suggest next prompt        │
├──────────────────────────────────────────────────────────────┤
│  Storage        — SQLite: catalog + experiment ledger          │
├──────────────────────────────────────────────────────────────┤
│  nlmx adapter   — subprocess to the ALREADY-GUARDED wrapper    │
│                   (idempotent import, no broken --source-ids,   │
│                    auto --confirm). One place for the guards.   │
├──────────────────────────────────────────────────────────────┤
│  nlm  (upstream) — auth, ~30 NotebookLM ops, Deep Research      │
└──────────────────────────────────────────────────────────────┘
```

Everything above the adapter is new. The adapter deliberately shells `nlmx`, not `nlm`,
so the corruption guards this repo already proved out apply to the MCP path too — not just
the CLI path.

**Zero-dependency MCP layer.** The MCP transport itself is bundled
(`mcp_stdio.StdioMCPServer`) — a stdlib-only JSON-RPC-over-stdio implementation of the
handshake + `tools/list` + `tools/call`. `_tools.py` defines the twelve tools once and binds
them onto that bundled server (default) or, optionally, the official `mcp` SDK (`--sdk`,
tolerant of 1.x `FastMCP` and 2.x `MCPServer`). The server fully loads and runs with
`pip install .` and no third-party package.

## The core loop: self-improving deep research

The v1 heart. Given a research **question** and a set of **prompt strategies**:

1. **Run** each strategy as its own NotebookLM Deep Research task (via `nlmx research run`),
   each into an isolated notebook so results don't cross-contaminate.
2. **Score data quality** of the sources each run pulled in — see the quality model below.
   This is *source* quality (what got retrieved), distinct from *answer* quality.
3. **A/B compare** the strategies on that quality signal, with the raw numbers shown, never
   a black-box verdict.
4. **Record** the experiment (question, variants, per-source scores, winner) to the ledger.
5. **Suggest** the next prompt: reuse the winning strategy for similar questions; mutate it
   for exploration. The corpus of experiments *is* the self-improvement.

### Data-quality model (transparent, tunable, no LLM required for v1)

A run's source set is scored on signals we can compute deterministically from
`nlm source list`/`source content`, so scores are reproducible and auditable:

| Signal | What it rewards | Cheap to compute? |
|---|---|---|
| **Authority** | primary sources, docs, papers, gov/edu, known-good domains; penalize content farms | yes (domain heuristics + allowlist) |
| **Diversity** | distinct domains / distinct authors — not 8 copies of one blog | yes (domain entropy) |
| **Recency** | fresher sources for time-sensitive topics (tunable per question) | yes (dates when present) |
| **Depth** | substantial content length vs. thin stubs | yes (char/token count) |
| **Relevance** | overlap of source text with the question terms | yes (BM25-lite / term overlap) |
| **Corroboration** | claims echoed across independent domains | partial (v2) |

Each signal is 0–1, combined by a weight vector that is **config, not code** — so a user tunes
"I care about recency and authority for news; depth and diversity for a lit review" without
forking. The composite is the run's `quality_score`. A/B ranks strategies by it.

LLM-judged answer quality is a **v2 add-on** (optional, costs tokens) layered on top of the
free deterministic score, never a replacement for it.

## Roadmap (each milestone is independently runnable)

- **M1 — Engine core (this PR).** `quality.py` + `ab.py` + storage + nlmx adapter, exposed as
  MCP tools `research.run_experiment`, `research.compare`, `quality.score_notebook`. Ships with
  tests that run *without* network (fixture source sets) so CI is green on a laptop.
- **M2 — Catalog.** ✅ `catalog_sync` / `catalog_search`: the Obsidian-alternative index
  over all notebooks + sources (SQLite + FTS5), so an agent asks "what do we already know
  about X" before researching. Sync is per-notebook fault-tolerant; search is full-text.
- **M3 — Strategy memory.** ✅ `research_learn_topic` (`research/strategy.py`): winners feed
  rule-based prompt mutations (authority/recency/breadth/depth) plus a per-topic tuned
  weight profile, nudged toward the signals the winning sources actually scored high on.
- **M4 — Multi-family sparring.** ✅ `research_spar` / `research_family_leaderboard`
  (`research/sparring.py`): several adversarial-runner CLIs (opencode, copilot, claude,
  codex, qoder, qwen) run the same question over one shared corpus; each family's result
  is scored and attributed, and the leaderboard ranks families by source quality.

## Non-goals

- Not reimplementing NotebookLM or `nlm`. We shell the guarded wrapper.
- Not a public multi-tenant service. NotebookLM MCP is single-account by design
  (see `skills/nlm-skill/references/remote-mcp.md`). Local-first, loopback-bound.
- Not a black box. Every score decomposes into the signals above; the ledger is plain SQLite.

## Self-evolving sources (`research/evolve.py`)

The corpus should *curate itself*: add fresh good sources, drop ones that go stale or
were always weak, and never let a notebook bloat. The mechanism is modeled on
**volcengine/OpenViking** (a starred, widely-used self-evolving context DB), whose
"self-evolving" is deliberately **not naive time-decay** — it is a
**create / merge / skip consolidation pipeline + score-ranked, budgeted recall**.
We adapt that to NotebookLM sources:

- **CREATE** — a novel candidate above the quality floor is added.
- **MERGE** — a candidate duplicating an existing source (same registrable domain +
  high title/content term overlap) is dropped in favour of the stronger of the pair.
- **SKIP** — a candidate below the floor that adds no diversity is not added.
- **EVICT** — an *existing* source whose **decayed** score has sat below the floor for
  `evict_after_strikes` consecutive cycles is proposed for removal. Decay is the
  "no fresh content over time" surrogate: each cycle multiplies a source's effective
  score by `decay` **unless it was refreshed** (score rose past `refresh_delta`), so a
  source nobody updates and that never wins its keep-slot fades out — exactly
  OpenViking's "low-score memories fade by never winning recall."
- **BUDGET** — the keep pool is ranked; only the top `keep_budget` survive.

`plan_evolution()` is **pure**: it returns a *plan* (a list of actions) and updated
per-source stats; it never calls NotebookLM. The caller executes the plan, so the one
destructive action (EVICT) always passes through the confirm gate. Every source gets
exactly one action per cycle, and the plan is fully auditable.

## Adversarial runners + skill evals (`research/evals.py`)

**Adversarial runners.** A strategy shouldn't be judged on one model family. Runners are
**pluggable adapters** for external agent CLIs — `opencode`, GitHub `copilot`, `claude`,
`codex` — **detected at runtime**: whatever isn't installed is reported `unavailable`
and skipped, never a hard failure. (On the reference machine `opencode` is present and
`copilot` is not, which is exactly the graceful-degradation case the design must handle.)
Adding a runner is a one-line `RunnerSpec`.

**Skill evals — is a skill getting better or worse?** `eval_skill_trend()` takes a
strategy's quality scores across successive experiments (from the ledger) and returns
`improving` / `regressing` / `flat` / `insufficient_data` via an OLS slope with a
`flat_band` so noise isn't read as a trend. The verdict travels **with the raw score
series** — "this skill is regressing −0.04/run" is a claim you can check. `volatility`
(stdev) is reported alongside as a stability signal.

Together these close the self-improvement loop: A/B picks a winner → the winner is used →
its scores accrue in the ledger → evals flag when it starts regressing → evolution keeps
the source corpus healthy underneath it.

## How sparring ranks families (`research/sparring.py`) — and its known edges

`run_sparring(question, prompt, families, runner)` runs the SAME prompt through
several model families over the SAME question, scores each family's returned
**source set** with the deterministic `score_sources` composite, and returns an
attributed, ranked `SparringSession`. A family is judged on *what it retrieved*,
not on prose — so the ranking is reproducible and token-free. Unavailable or
crashing families are recorded, never allowed to sink the session.

**Verified property — quality beats volume.** A family returning 2 authoritative
sources beats one dumping 6 mid-tier same-domain sources (composite 0.699 vs 0.354
on the probe fixtures). Authority dominates and the set-level `diversity` term
*punishes* many near-identical low-authority hosts (its `distinct_ratio` scaling
drives 6-blogs-one-shape toward 0). This closes the obvious gaming vector — you
cannot win sparring by returning volume. Pinned by
`tests/test_sparring_scoring_design.py::test_few_excellent_beats_many_mediocre`.

**Known limitation — relevance is lexical.** `relevance_score` is question-term /
source-content overlap. A genuinely on-topic source that uses *synonyms*
("surface codes / fault tolerance" for "quantum error correction") scores **0.0**
on relevance, which is weighted 0.20 in the composite — so real-world sources that
don't lexically echo the query are systematically under-scored. This is documented
and pinned (`::test_relevance_is_lexical_documented_limitation`) rather than
silently patched, so a future semantic-relevance upgrade (embeddings / term
expansion) is a deliberate, reviewed change instead of a silent shift across every
historical ledger score. Until then, prefer questions that use the field's own
vocabulary, or raise `authority`/`depth` weights relative to `relevance` via
`QualityWeights` when a corpus is known to be synonym-heavy.
