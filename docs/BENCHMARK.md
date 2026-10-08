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
