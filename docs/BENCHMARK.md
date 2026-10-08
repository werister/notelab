# Benchmark: agent researches alone vs. agent drives NoteLab

One task, two arms, run through whichever agent CLIs were installed. **One trial per cell,
2026-10-08.** Treat this as a smoke test, not a study.

**Task (both arms):** a ~200-word briefing on *the state of quantum error correction: key
experimental results and open problems*, citing at least 5 distinct sources by URL.

- **A, alone:** the agent uses its own web search/fetch tools, opens and reads sources, writes the briefing.
- **B, NoteLab:** the agent runs `nlmx research run "<question>" --notebook-id <id> --mode fast` (NotebookLM searches the web and imports ~10 sources on its side), then `nlmx notebook query <id> "<task>"` and prints the cited answer.

Harness: [`scripts/bench_research.py`](../scripts/bench_research.py). Each run starts in an empty temp directory.

## Results

| Agent CLI | Arm | Time | Model turns | Output tokens | Cost | Words | URLs cited |
|---|---|---|---|---|---|---|---|
| **claude** | A, alone | 59 s | 12 | 2,254 | $0.26 | 282 | 7 |
| **claude** | B, NoteLab | 126 s | 3 | 1,038 | $0.18 | 203 | 7 |
| kilo (default perms) | A | 109 s | n/a | n/a | n/a | 227 | 5 |
| kilo (`--auto`) | B | 134 s | n/a | n/a | n/a | 196 | 5 |
| kilo (default perms) | B | no signal: the shell call was auto-rejected | | | | | |
| opencode (free model) | A | timed out at 900 s, no answer | | | | | |
| opencode (free model) | B | 319 s, upstream 504 at the end, but an answer with 7 URLs was produced | | | | 358 | 7 |
| copilot, kiro-cli, qoder, codex | A and B | no signal: monthly or credit usage limit reached before any work | | | | | |

Only Claude's CLI reports tokens and cost in headless mode, so that row pair is the only
measured cost comparison.

## What the Claude pair shows

- The NoteLab arm needed **3 model turns instead of 12**, about **54% fewer output tokens**, and cost about **32% less** ($0.18 vs $0.26).
- It was **slower** (126 s vs 59 s) because NotebookLM's research step takes about a minute and a half on its own.
- Both produced a briefing with 7 cited URLs. We did not grade factual accuracy of either.

## What it does not show

- **Not a statistical result.** One run per cell. Cost also depends on prompt caching, and the two arms started with different cache states.
- **The saving is partly offloaded, not removed.** Reading the sources happened inside NotebookLM, so that compute still exists; it just isn't billed to the agent's context.
- **Small task.** About 10 sources, not 70 PDFs. The gap should grow with source count, since arm A re-reads everything and arm B does not, but we have not measured it.
- **Four of the nine CLIs gave no signal** because their accounts were out of quota, and two others hit permission or upstream errors. They say nothing about NoteLab either way.
- **Permission settings differed by tool.** Claude used scoped tool allowlists (`WebSearch WebFetch` for A, `Bash(nlmx:*)` for B). Kilo's B run needed blanket `--auto` in an empty temp directory.
- **Answer quality was not graded.**

## Reproduce

```bash
nlm login --check
BENCH_OUT=my-results.jsonl python3 scripts/bench_research.py claude A
BENCH_OUT=my-results.jsonl python3 scripts/bench_research.py claude B   # creates notelab-bench-claude
```
