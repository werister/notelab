# Exploration — GitHub repos as notelab sources, and code-snippet trust (2026-09-24)

Prompted by: "how could we get GitHub repos in there, how would agents query proper code
snippets, and how do we evaluate their accuracy/value?" Answered by actually trying it — every
claim below is a live, reproduced result against a real NotebookLM notebook, not a guess from
docs. Notebooks created for these tests were deleted afterward; nothing persists from this
exploration except this doc and the fixes it led to.

## 1. There is no native "add a GitHub repo" — you preprocess

`nlm source add --help` supports exactly five source types: `--url`, `--text`, `--drive`,
`--youtube`, `--file`. No repo/git-aware type. Two of these can reach GitHub content:

- **`--url <raw-or-blob-url>`** — NotebookLM fetches and renders the page itself. Gets a real
  domain (`github.com` / `raw.githubusercontent.com`), which matters (§3). You don't control
  what NotebookLM's fetcher extracts from the page.
- **`--text <content>`** — you fetch the file yourself (clone, `gh api`, whatever) and push the
  exact bytes. Full control over *what* gets ingested. **Confirmed live: `--text` and `--url`
  are mutually exclusive** (`nlm source add --text ... --url ... ` → `Error: Please specify only
  one source type at a time`) — you cannot push your own verified content AND tag it with a
  meaningful URL/domain identity in one call. Pick one.

**Practical recipe for "get a repo in there":** write a small script that walks the repo (a
shallow clone or the GitHub API), picks the files worth ingesting (READMEs, design docs, and/or
source files, size-bounded), and calls `nlm source add <nb> --text "$(cat file)" --title
"<repo>/<path>"` per file. This is real, doable today with zero new tooling — but see §3 and §4
before treating the result as reliable for code.

## 2. `nlm notebook query`'s answers don't hallucinate on a clean negative — good sign

Asked a notebook containing only `nlmx`'s own source "show me the exact code of
`create_notebook`" (a function that lives in `nlmx_adapter.py`, not `nlmx` — deliberately not in
the ingested file). It correctly answered **"not present"** and listed the functions that
actually are there, rather than fabricating one. One data point, not a guarantee, but a good
sign for this specific failure mode (confabulating a plausible-looking function that isn't real).

## 3. Citations and even raw `source content` do NOT preserve code formatting — verified, quantified

This is the load-bearing finding. Ingested `nlmx`'s own 203-line, 7485-byte source as a `--text`
source, then compared three retrieval paths against the real file:

| Path | Result |
|---|---|
| `notebook query` citations (`cited_text`) | Whitespace/newlines collapsed to single-line "paragraphs" wherever the original had markdown-like patterns (backtick `` `code` `` spans, `<placeholder>` tokens) |
| `source content` (documented as "no AI processing") | Same problem, worse: 7141 of 7485 chars survived (not truncated — the ~5% delta is backtick-stripping), but **structurally reflowed**: 435 lines back out of 203 real lines, with real newlines inserted *inside* what were single logical lines |
| Plain code (no docstrings/backticks/`<x>` nearby) | Survived close to verbatim |

Concretely, the real source:
```python
def load_ledger():
    if LEDGER_PATH.exists():
        return json.loads(LEDGER_PATH.read_text())
    return {}
```
came back from `source content` as one flattened string: `"def load_ledger(): if
LEDGER_PATH.exists(): return json.loads(LEDGER_PATH.read_text()) return {}"` — **syntactically
invalid Python** if pasted as-is (indentation is semantically load-bearing in Python; this
reflow strips it). The corruption isn't random — it correlates specifically with markdown-like
syntax in the source text (inline code spans, angle-bracket placeholders), which NotebookLM's
ingestion pipeline appears to parse and re-render as if it were prose/markdown rather than
preserving as opaque text.

**Implication: neither `notebook query` nor `source content` can be trusted to return
byte-exact, directly-usable code.** This isn't a corner case you can code around by asking more
carefully — it's baked into how NotebookLM's pipeline processes ingested text.

## 4. Existing-source dedup is structurally blind to `--text` sources — verified

`research_evolve_notebook`'s duplicate-merge logic (`research/evolve.py::_similarity`) requires
`require_domain_match=True` for existing-source comparison: `same_domain = bool(a.registrable)
and a.registrable == b.registrable`. A `--text` source has no URL, so `a.registrable` is empty,
so `same_domain` is always `False`, so **similarity is forced to 0.0 regardless of how identical
the content actually is.**

Verified directly: added two near-identical paragraphs about Python list comprehensions (same
facts, ~80% shared vocabulary, deliberately paraphrased) plus one clearly off-topic source to a
test notebook, ran `research_evolve_notebook(apply=true)`. Result: **all three kept, zero
merges, zero evictions** — the near-duplicate was invisible to the merge logic for exactly the
reason above.

This directly matters for code ingestion: if you push two versions of the same file (different
branches, a vendored copy, a stale snapshot next to a fresh one) as `--text` sources, notelab's
own curation loop cannot detect or collapse that duplication. Not a one-line fix — the domain
gate exists deliberately (see the docstring: it prevents two independent sources that happen to
discuss the same topic from being wrongly collapsed into one). A real fix needs a
content-origin-aware identity signal for text sources (e.g., pass the originating repo+path as a
synthetic domain key) rather than just relaxing the gate, which would reintroduce the
false-merge risk the gate exists to prevent. Left as an open design question, not fixed here.

## 5. `catalog_search` only indexes titles/urls, not content (now fixed for recall — still title-only)

Separately found and fixed live (see `docs/FIELD-NOTES.md` gap in the commit history): the FTS5
catalog search was AND-only, unstemmed, and mishandled hyphenated terms, so natural queries like
"PSD2 aggregator" returned zero hits against a real, matching catalog. Fixed with porter
stemming + OR/prefix matching. **Still relevant here:** `search_catalog`'s docstring is explicit
that it indexes "source titles/urls" only, never source *content*. For code, this means
`catalog_search "list_ledger"` would only find a source if the literal string "list_ledger"
appears in that source's **title** (e.g., you titled it `nlmx::load_ledger`) — it will never
search inside the code itself. Titling convention matters a lot if you want this to double as a
code index.

## Recommendation: don't use NotebookLM for exact code, use it for prose-about-code

Given §3 and §4, the honest scoping is:

- **Good fit for notelab/NotebookLM:** broad synthesis over prose — READMEs, design docs,
  architecture-decision writeups, issue/PR discussion, competitor comparisons, "why was X chosen
  over Y." This is exactly what the finance-app research (BaFin regulatory text, FinAPI/Tink
  docs, competitor teardown) already proved works well — real, accurate, well-cited synthesis
  over documents where exact formatting doesn't matter.
- **Bad fit: exact/executable code retrieval.** A query like "show me function X" can return
  text that *looks* like code but has been silently reformatted in a way that breaks
  execution. Never paste a notelab/NotebookLM answer's code directly into a codebase without
  independently verifying it against the real file.
- **The right tool for "agents querying proper code snippets" already exists in this stack**: a
  structural code-graph tool that reads files directly and returns exact byte ranges by
  qualified name (this session has `codebase-memory-mcp`'s `search_graph` / `get_code_snippet` /
  `trace_path` — purpose-built for exactly this, with no LLM reflow in the retrieval path at
  all). notelab's contribution to a code-research workflow is better aimed at the "why"
  (design docs, commit/PR rationale, comparisons across repos) layer, handing off to a
  graph/grep-based tool for the "what does the code actually say right now" layer.

## How to evaluate accuracy/value of a code snippet, concretely

1. **Never trust a notelab/NotebookLM answer as ground truth for code.** Treat it as a pointer
   (which file, which function) and re-fetch the real thing before using it — exactly the same
   "verify, don't assume" discipline `docs/DESIGN.md` already applies to source quality.
2. **Verbatim-match check, if you must go through NotebookLM:** before trusting any returned
   code text, confirm it's a literal substring of the actual current file. If it isn't
   byte-identical, don't use it as code — treat it as a (possibly-corrupted) paraphrase.
3. **Extending `quality.py`'s signal model to code-as-source**, if this becomes a real feature
   rather than an exploration: keep authority/diversity/recency/depth/relevance, and add
   code-specific signals a doc source doesn't have —
   - **canonicality**: is this the current default-branch content, not a stale fork/branch copy?
   - **verification**: does it round-trip byte-exact against the real file (the check in #2)?
   - **usage/maintenance**: recency of the file's own last commit, not just when it was ingested.
   None of this is implemented; it's a sketch for if/when code ingestion becomes a real notelab
   feature rather than this one-off exploration.

## Bottom line

Getting a GitHub repo "into" notelab is mechanically possible today (script + `--text`), but the
result is good for *prose research about the repo*, not for *exact code retrieval* — and the
existing curation/dedup/search machinery (evolve, catalog) wasn't built with code-as-source in
mind, so it silently under-performs on it (no dedup, title-only search) rather than failing
loudly. If code-repo ingestion becomes a real roadmap item, it needs its own dedup identity
scheme and its own retrieval path (bypassing `notebook query`/`source content` for anything that
has to be byte-exact) — not just pointing the existing pipeline at more `--text` sources.
