# Benchmark: reading a paper corpus yourself vs. asking a notebook

NoteLab's value is not one-off research. It is having a corpus of papers loaded **once**,
then asking specific questions of it again and again. So the main benchmark fixes the
corpus and compares how an agent answers questions over it. A first, weaker benchmark
(fresh research on one topic) is at the bottom, kept for honesty.

**Single trial, 2026-10-08, Claude's CLI.** It is the only CLI that reports tokens and cost
headlessly. Treat this as a smoke test, not a study.

## Setup

- **Corpus:** 29 arXiv papers on quantum error correction experiments (37 MB of PDFs), the top results of an arXiv search, not hand-picked.
- **Questions:** 5 specific ones, for example *"which decoding approaches are used across the papers, and which paper uses each?"* ([list](../scripts/bench_corpus.py)).
- **Arm A, read it yourself:** the agent gets a folder of the PDFs and `Read`/`Glob`/`Grep`.
- **Arm B, NoteLab:** the same 29 papers are already in a NotebookLM notebook. The agent runs `nlmx notebook query` once per question. **No research step runs.** Loading the corpus took 185 s, once, with no model tokens, and is excluded from the numbers below, as it would be amortized over every later question.

## Results (Claude)

| | A: read PDFs | B: NoteLab notebook |
|---|---|---|
| Cost | **$0.57** | **$0.25** |
| Model turns | 43 | 8 |
| Output tokens | 8,654 | 2,772 |
| Input tokens processed (incl. cache) | ~659,000 | ~191,000 |
| Wall time | 106 s | 247 s |
| Coverage | Read page 1 of all 29 papers and a few later pages; said it did not check most results sections | Answered from the indexed full text, citing papers per claim |

NoteLab was about **2.3x cheaper** and used **~3.4x fewer input tokens** on this run, with
the agent doing 8 turns instead of 43. It was **slower**: each notebook query takes tens of
seconds on NotebookLM's side.

## Why this probably understates the gap

- **Arm A cut corners.** It read the first page of each paper and said so. A careful reader would load far more pages, and cost would climb with every page. Arm B's cost does not grow with paper length.
- **Questions repeat.** Arm A re-reads for each new question set; arm B only pays for the new queries. We ran one set, so this is not in the numbers.
- **29 papers, not 70.** Arm A scales with corpus size; arm B mostly does not.

## What it does not show

- **One trial.** No error bars. Prompt caching differs between arms.
- **Answer correctness was not graded.** Arm B's notebook mangled some code labels into citation markers, and the agent had to flag that. Neither arm was checked against the papers.
- **Offloaded, not eliminated.** The reading still happens inside NotebookLM; it just is not billed to the agent's context. NotebookLM has its own source and usage limits.
- **Other CLIs:** copilot, kiro-cli, qoder and codex hit usage limits and gave no signal; kilo, opencode and muse ran the fresh-research task below but report no tokens, so they have no cost comparison.

## Reproduce

```bash
# one-time: load your papers into a notebook, keep the PDFs in ./pdfs
nlm notebook create "bench-corpus"            # note the id
for u in $(cat urls.txt); do nlm source add <id> --url "$u"; done

BENCH_OUT=r.jsonl python3 scripts/bench_corpus.py A claude --papers ./pdfs
BENCH_OUT=r.jsonl python3 scripts/bench_corpus.py B claude --notebook <id>
```

---

## Coding: questions over several repos

Does the same idea help when the corpus is code, and the questions are the kind you ask to get
unstuck? Two real repos, click and flask (~22k lines), in one place; 8 questions mixing
problem-solving ("I want a Flask CLI command that prompts for a hidden password: which Flask and
click pieces, in which files?"), cross-repo flow ("how does `flask` find and load the app?"),
exact lookups, a verbatim-code request, and one question about a function that does not exist.
I checked every answer against the source. Claude's CLI, one trial. Harness:
[`scripts/bench_code.py`](../scripts/bench_code.py).

| Arm | What the agent has | Correct | Cost | Turns | Input tokens (incl. cache) | Time |
|---|---|---|---|---|---|---|
| **A** | the two repos on disk, `Read`/`Glob`/`Grep` | **8 / 8** | $0.22 | 5 | ~214k | **45 s** |
| **B** | a notebook of the 29 raw source files (click + flask `src`, as sources) | **8 / 8** | $0.21 | 9 | ~88k | 241 s |
| **D** | a notebook with only what `research_ingest_codebase` produces (a ~2 KB digest per repo) | **0 / 8** | $0.16 | 9 | ~78k | 144 s |

What this says:

- **For code at this size, a plain agent with grep is already as good and about 5x faster.** Both A and B got all 8 right, including the verbatim `make_str` (byte-identical) and the nonexistent function (both said so plainly). Cost was a tie. B used fewer input tokens, but notebook queries are slow and cost extra turns. This is the opposite of the paper result: code is searchable by name, papers are not.
- **The ingest digest alone is not enough to ask questions.** It lists file counts, key-file names and function names, no source. Arm D could answer nothing, and it said so honestly rather than inventing answers. That is a good failure, but the tool's output does not support question-asking as shipped. Putting the raw files in as sources (arm B) works.
- **No hallucinations** in any arm on the nonexistent-function question.
- **Verbatim code survived here** for a plain function. `docs/CODE-INGESTION-EXPLORATION.md` documents corruption for code containing markdown-like syntax (backticks, `<x>` placeholders), so one clean function does not clear that.

Where a notebook of repos could still win, which this run does not test: far more files than an agent can grep economically, many repos at once, mixing in issues, docs and prose, and reuse across sessions. At 2 repos and ~22k lines it did not win on cost or speed.

### Round 2: targeted lookups and versions, with a local code index

After round 1, NoteLab gained a local code index (`code_find`, `code_versions`, `code_diff`;
see the README). Six targeted questions over click 8.1.7, click main and flask: every call
site of `getpass`, callers of a method, the verbatim source of a method, direct subclasses of a
class, the signature of `Context.forward` in two versions, and which classes were added between
versions. Checked against `grep`/`diff` ground truth I computed first.

| Arm | What the agent has | Correct | Cost | Turns | Time |
|---|---|---|---|---|---|
| **A** | both click versions and flask on disk (I pre-checked-out the old version), `Read`/`Glob`/`Grep` | **6 / 6** | $0.19 | 9 | 37 s |
| **C** | only the local index CLI | **6 / 6** | $0.25 | 15 | 43 s |

Honest reading: **a grep-capable agent is still as accurate and slightly cheaper here.** The
index did not beat grep on cost or speed. Arm C needed more turns because it queries one thing
at a time and, early on, hedged about what it hadn't checked.

Two things this run is not fair to the index about, and one it is fair about:

- **Not fair to the index:** I handed arm A a ready-made checkout of 8.1.7. In real use that is a
  `git worktree` per version you must create; the index keeps every version after one `index` call.
- **Not fair to the index:** the first arm C attempt failed on my own setup errors and then did worse
  ($0.29, 25 turns) before I documented the `subclasses` and `diff` commands to the agent and
  added the `subclasses` lookup, which did not exist. Second attempt shown above. That tuning
  after seeing a result is a bias in the index's favour, so treat its numbers as a ceiling.
- **Fair:** it is a different tool from grep, not a faster one. Its claim is exactness and
  retained history (`diff` gave the full added/removed/changed function lists in one call), not speed.

---

## Appendix: fresh research on one topic (the weaker benchmark)

Task: a ~200-word briefing on *the state of quantum error correction*, 5+ cited URLs.
Arm A: the agent searches and reads the web itself. Arm B: `nlmx research run`, then one query.
This charges arm B for the research step, which is not how NoteLab is meant to be used
repeatedly, so treat it as a worst case for NoteLab. Harness: [`scripts/bench_research.py`](../scripts/bench_research.py).

| Agent CLI | Arm | Time | Turns | Output tokens | Cost | Words | URLs |
|---|---|---|---|---|---|---|---|
| claude | A | 59 s | 12 | 2,254 | $0.26 | 282 | 7 |
| claude | B | 126 s | 3 | 1,038 | $0.18 | 203 | 7 |
| muse | A | 131 s | n/a | n/a | n/a | 170 | 6 |
| muse | B | 159 s | n/a | n/a | n/a | 162 | 6 |
| kilo | A | 109 s | n/a | n/a | n/a | 227 | 5 |
| kilo (`--auto`) | B | 134 s | n/a | n/a | n/a | 196 | 5 |
| opencode (free model) | A | timed out at 900 s | | | | | |
| opencode (free model) | B | 319 s, upstream 504 at the end, answer produced | | | | 358 | 7 |
| copilot, kiro-cli, qoder, codex | A, B | no signal: usage limit reached | | | | | |
