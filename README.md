# NoteLab 🧪

**Research that gets better every run.**

![NoteLab in 20 seconds: the guard says no, the loop learns, 269 green, 6/6 live agent evals](brag-output/demo.gif)

*20 seconds, every frame real output: a guard refusing a corrupting re-import, the
self-improving loop picking a winner, the suite green, live agents stopped 6/6.
[Full video with sound](brag-output/brag.mp4).*

Most "NotebookLM MCP" integrations are a *remote control* — create notebook, add source,
query. NoteLab is the layer above that: it treats **deep research as an experiment that
gets better every time you run it.** It scores the *data quality* of the sources a research
run pulls in, A/B-tests the prompts that produce them, keeps the winners, curates the
corpus (adding fresh good sources and evicting stale ones), spars model families against
each other, and remembers what works — per topic, over time.

It is built on a hardened base: a guarded wrapper (`nlmx`) around
[`nlm`](https://github.com/jacob-bd/notebooklm-mcp-cli) that intercepts three confirmed
NotebookLM corruption bugs — including a re-import that duplicates every source behind a
byte-identical "✓ Imported" message — so the whole research loop runs on a floor that
won't silently duplicate or lose your sources. And the guards aren't just unit-tested:
live agents from two model families were pointed at them and stopped **6/6**
([proof](#live-proof-real-agents-vs-the-guards)).

- **Engine + MCP server are stdlib-only** — `pip install .` gives a runnable server with
  zero third-party dependencies (bundled JSON-RPC stdio MCP, no external SDK required).
- **302 offline tests, 98% coverage** (2 optional live tests skipped without keys),
  no NotebookLM account needed to run them. CI on py3.10/3.12/3.13.
- **See the whole loop in 5 seconds:** `python examples/self_improving_loop.py`

---

## 🧪 The research server — self-improving deep research

[![tests](https://github.com/werister/notelab/actions/workflows/tests.yml/badge.svg)](https://github.com/werister/notelab/actions/workflows/tests.yml)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![deps](https://img.shields.io/badge/engine-stdlib--only-informational)

Most "NotebookLM MCP" integrations are a *remote control*: create notebook, add source, query. `nlm` already does that. This server adds the layer that's actually missing — **it treats deep research as an experiment that gets better every time you run it.**

Give it a question and a few prompt strategies. It runs each as its own NotebookLM Deep Research task (through the guarded `nlmx`, so no duplicate-import corruption), **scores the data quality of the sources each one pulled in**, A/B-ranks the strategies on that signal, and **remembers the winner** so your research prompts stop being guesses and become tuned instruments.

> **See the whole loop in 5 seconds, no NotebookLM account:** `python examples/self_improving_loop.py`

<details><summary><b>What that demo actually prints</b> (real output, click to expand)</summary>

```
1. A/B — score prompt strategies on SOURCE data quality
   winner: authoritative  (score 0.827, margin 0.6748)
     authoritative   quality=0.827    n_sources=3
     broad           quality=0.1522   n_sources=2

2. Ledger — record it, then reuse the winning prompt for a repeat question
   recorded experiment #1
   suggested prompt next time: 'find authoritative primary quantum sources'

3. Evolve — curate the notebook's sources over two cycles
   -- cycle 1 -- {"kept": 2, "create": 1, "merge": 1, "skip": 1, "evict": 0}
     merge   dupe      -> keep1   # duplicate of existing source (similarity ≥ 0.82)
     skip    new_weak            # below quality floor (0.13 < 0.35)
     create  new_good            # novel and above floor
   -- cycle 2 -- {"kept": 1, "create": 0, "merge": 1, "skip": 0, "evict": 1}
     evict   stale               # weak for 2 consecutive cycles — stale / no fresh content

4. Evals — is the 'authoritative' strategy improving or regressing?
   trend: improving  (+0.081/run, Δ +0.320)   scores: [0.34, 0.41, 0.49, 0.58, 0.66]
```

The authoritative prompt beats the broad one **0.83 vs 0.15** on source quality; the
duplicate is merged, the novel good source created, the weak one skipped; and by cycle 2
the stale source is **evicted** for going quiet. That's the self-improving + self-evolving
loop, end to end, deterministically.
</details>

**Tuning:** pass a weight preset (`news`, `lit_review`, `docs`, `balanced`) or your own
weights as `weights` — see [docs/CONFIG.md](docs/CONFIG.md).

> An indexed, self-tuning research memory for agents — NotebookLM as the retrieval/synthesis engine, this server as the scientist that decides *which way of asking* produces good sources, and remembers.

### Why it's worth running

- **Measures source quality, transparently.** Every run's sources are scored on five signals — **authority** (primary/gov/edu/papers vs. content farms), **diversity** (domain entropy, not 8 copies of one blog), **recency** (configurable half-life), **depth** (substance vs. stubs), and **relevance** (term overlap with your question). The composite decomposes into every signal — never a black-box verdict.
- **Weights are config, not code.** "Recency + authority for news; depth + diversity for a lit review" is a weight vector you pass in, no fork.
- **The A/B loop is the product.** `research_run_experiment` runs N prompt variants into isolated notebooks, ranks them by source quality, and records the experiment to a plain SQLite ledger. `research_suggest_prompt` reuses the winner next time.
- **Runs green on a laptop.** The engine is stdlib-only; the full test suite is offline (fixture source sets), so CI passes with no network and no NotebookLM account.

### Quickstart

```bash
# Install — the engine AND the MCP server are stdlib-only. No third-party deps.
pip install -e .

# Run the bundled MCP server (stdio JSON-RPC — what Claude Desktop / Cursor / Copilot speak)
nlm-research-mcp
```

That's it — the server **fully loads on its own**; it does not import or require the
external `mcp` SDK. (If you prefer the official SDK, `pip install -e ".[mcp]"` and run
`nlm-research-mcp --sdk`; that path supports both the 1.x `FastMCP` and 2.x `MCPServer`
APIs.)

**On `nlm`:** the tools that hit Google NotebookLM shell the guarded `nlmx` wrapper
(below) at call time, so install that if you want live research. Everything else —
the quality scorer, A/B ranking, self-evolving planner, evals, the catalog index, and
the whole MCP protocol layer — is bundled in this repo and runs with no external
NotebookLM install. Tools degrade with a clear error when `nlmx` isn't present rather
than failing to load.

`nlmx` is a thin wrapper around the upstream
[`notebooklm-mcp-cli`](https://pypi.org/project/notebooklm-mcp-cli/) (`nlm`) — it does
not reimplement auth or the NotebookLM API, so `nlm` itself has to be installed and
logged in first, before `nlmx`/this server can do anything real:

```bash
# 1. Install the upstream nlm CLI (isolated tool install, not into this venv)
pipx install notebooklm-mcp-cli      # or: uv tool install notebooklm-mcp-cli
nlm --version                        # confirms it's on PATH

# 2. Authenticate once — launches a browser, extracts + saves session cookies
nlm login
nlm login --check                    # confirms the session is valid

# 3. Now install the guard this server actually calls (see "nlmx" below)
cp nlmx ~/.local/bin/nlmx && chmod +x ~/.local/bin/nlmx
```

Saved cookies are typically good for weeks; re-run `nlm login` if a tool call reports
expired/invalid auth. `nlm` remains the source of truth for authentication — see
[jacob-bd/notebooklm-mcp-cli](https://github.com/jacob-bd/notebooklm-mcp-cli) for its
own docs, and `skills/nlm-skill/SKILL.md` in this repo for the fuller auth-recovery
flow (used by agents, not just humans).

Register it with your agent (Claude Code example):

```json
{ "mcpServers": { "nlm-research": { "command": "nlm-research-mcp" } } }
```

### MCP tools (v1)

| Tool | What it does |
|---|---|
| `research_run_experiment(question, strategies, weights?)` | A/B several prompt strategies; score each run's sources; rank; record to ledger |
| `research_score_notebook(notebook_id, question?, weights?, semantic_expansions?)` | Score the data quality of an existing notebook's sources (`semantic_expansions` opts into Mistral-embedding relevance; needs `MISTRAL_API_KEY`) |
| `research_history(experiment_id?, limit?)` | Browse the experiment ledger, or fetch one full result |
| `research_suggest_prompt(question)` | Reuse the winning prompt from a prior experiment on the same question |
| `research_evolve_notebook(notebook_id, question?, keep_budget?, quality_floor?, apply?)` | Self-evolving sources: plan create/merge/skip/evict + budget over a notebook's sources (OpenViking-style consolidation; evictions stay behind the confirm gate) |
| `research_assess(notebook_id, question?, mode?, weights?, use_laya?, semantic_expansions?)` | Value-assess a notebook's current sources: `fast` (cheap good/thin/bloated verdict + coverage gaps, the same check every `research_run_experiment` arm now runs automatically) or `deep` (full ablation + redundant-pairs + greedy-prune) |
| `research_eval_skill(strategy_name)` | Is a strategy improving or regressing over its recorded history? OLS-slope trend + raw series |
| `research_adversarial_run(prompt, runners?, timeout?)` | Run a prompt through installed adversarial agent CLIs (opencode, copilot, claude, codex, qoder, qwen, …); missing ones skipped, not failed |
| `catalog_sync(notebook_ids?)` | Index your notebooks + sources into the local catalog (the Obsidian-alternative index) |
| `catalog_search(query, limit?)` | Full-text search the index — "what do we already know about X" — before researching again |
| `research_learn_topic(question, explore?)` | M3 strategy memory: turn a topic's experiment history into next-round prompt mutations + a tuned weight profile |
| `research_spar(question, prompt, families?)` | M4 multi-family sparring: run the same prompt through several model-family CLIs over one shared corpus, score and attribute each |
| `research_family_leaderboard()` | Rank model families by source quality — which family finds the best sources for which kind of question |
| `research_ingest_codebase(repo, notebook_id?, title?, max_files?)` | Ingest a codebase (GitHub URL or local path) and add its structure/API digest as a NotebookLM source; the ADD to `research_evolve_notebook`'s prune |

### Live proof: real agents vs. the guards

Unit tests prove the guard *logic*; the eval harness
(`python -m notelab.research.guard_evals --notebook-id <id> --source-id <sid>`)
proves a live agent reasoning for itself actually gets stopped — the real threat
model. Every scenario is read-only: `nlmx` refuses before ever calling `nlm`.
Latest sweep (2026-10-07):

| runner | `--source-ids` refusal | `chat start` refusal | missing-notebook fail-fast |
|---|---|---|---|
| opencode | ✅ PASS | ✅ PASS | ✅ PASS |
| claude | ✅ PASS | ✅ PASS | ✅ PASS |

6/6 across two model families. The scorer only counts substantive refusal
markers — a bare command echo doesn't pass — and runs that can't produce a
signal (Copilot: monthly quota exhausted; Codex: usage limit; a shell-less
Claude spec: approval-blocked) are reported as environment errors, never as
passes or failures. Honest scoreboard, honest harness.

### Agent skills

- `skills/nlm-skill/` — operating NotebookLM (the remote control).
- `skills/research-loop/` — researching *through* notelab: the 14 tools, the
  loop, three worked flows. This is the skill for "research X and get better".
- `skills/skill-evolution/` — the champion/challenger protocol that makes
  skills themselves measurable: fixed batteries, margin + held-out + green-tree
  gating, git-archived scores. Duel a prompt variant with
  `skills/skill-evolution/scripts/evolve.py --battery <file> --challenger <v>`.

### Design & roadmap

Full architecture, the data-quality model, and the milestone plan (catalog / strategy-memory / multi-model-family sparring) are in [docs/DESIGN.md](docs/DESIGN.md).

### Develop

```bash
pip install -e ".[dev]"
pytest -q          # offline, no NotebookLM account needed
```

---

## `nlmx` — the guarded wrapper

The self-improving server above runs on top of `nlmx`. Here's what `nlmx` guards and why.

## The investigation

Three agentic CLIs were each given the same nlm task, independently, on isolated notebooks, and their outputs were verified against ground truth (`nlm source list`) rather than trusted at face value.

### 1. Install gap: none of the three had nlm wired in correctly out of the box

| Client | `nlm setup add <x>` (MCP) | `nlm skill install <x>` (skill file) |
|---|---|---|
| Claude Code | **Silent no-op** — PATH-detection bug looks for a `claude` binary that isn't on PATH even inside a live Claude Code session | Works — correct convention, auto-discovered |
| Cursor | Writes a real config, but points at a bare `notebooklm-mcp` command not resolvable from the app's inherited PATH | Works, but Cursor's own agent couldn't confirm the skill directory is actually auto-loaded |
| Copilot CLI | **Wrong target** — writes VS Code's `.vscode/mcp.json`, not the standalone CLI's real config | Never installed at all — nlm has no real integration with the standalone Copilot CLI |

Root cause for the Claude/Cursor failures: the underlying `notebooklm-mcp` binary exists but isn't on the PATH any GUI-launched or restricted-shell process inherits. Fixed by pointing every MCP config at the absolute binary path instead of a bare command.

### 2. `research import` is not idempotent

`nlm research start` → `research status` → `research import` is a three-step state machine — sources never land in the notebook without the explicit `import` call. Confirmed, independently, three separate times on isolated notebooks: **calling `import` a second time on an already-imported task duplicates every source**, with a byte-identical "✓ Imported N sources" success message both times. There is no textual signal that anything went wrong — the only way to know is to independently count sources before and after.

### 3. `--source-ids` on `notebook query` is broken

Documented as "Comma-separated source IDs to query (default: all)". In practice, passing it **deselects every source instead of scoping to the one given** — `sources_used: []`, `"No sources are currently selected"`. Reproduced directly, consistently. Omitting the flag (querying all sources) works fine.

### 4. Generation commands need `--confirm`/`-y` or they silently block/no-op
Documented upstream, but easy for an agent to forget in a non-interactive context — confirmed it fails safe on EOF (doesn't hang forever) but still needs the flag to actually do anything.

## The fix: `nlmx`

A ~200-line Python wrapper, stdlib only, that delegates everything to real `nlm` unchanged except:

```
nlmx research import <notebook> [task_id]   # refuses a repeat import via a local ledger, unless --force
nlmx research run "<query>" --notebook-id <id> [--mode fast|deep]   # new composite: start→poll→import, exactly once
nlmx notebook query <notebook> "<question>" --source-ids <id>   # refused outright — points at the real workaround
nlmx <type> create <notebook> ...           # auto-injects --confirm if you forget it
nlmx chat start ...                         # refused outright — chat start is an unusable REPL for an agent
nlmx <anything else>                        # pure passthrough to `nlm`
```

## Install

```bash
cp nlmx ~/.local/bin/nlmx && chmod +x ~/.local/bin/nlmx
```

Then install the skill for whichever agent you use:

```bash
# Claude Code
cp -r skills/nlm-skill ~/.claude/skills/nlm-skill

# Cursor
cp -r skills/nlm-skill ~/.cursor/skills/nlm-skill

# Copilot CLI
copilot skill add skills/nlm-skill
```

The skill file is nlm's own upstream `nlm-skill` (installed via `nlm skill install <client>`), with one added section pointing agents at `nlmx` and explaining exactly why, backed by the evidence above — not just "trust me."

## Why a wrapper instead of a fork

`nlm` already implements ~30 working subcommands, auth/cookie handling, and retry logic. Reimplementing that to fix two behaviors would mean owning all of it. This wraps it instead — pure passthrough for everything that already works, guards only where the docs alone can't stop a silent corruption.
