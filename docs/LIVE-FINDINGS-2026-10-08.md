# Live NotebookLM findings & refinement proposals — 2026-10-08

This records a session where NoteLab's full live path was exercised against **real**
Google NotebookLM (throwaway account, via `nlm login --manual` cookie auth), and turns
what the real data showed into concrete, prioritized refinement proposals.

Every number below is from live output, not a fixture.

## What was verified against the real API

| Capability | Result on live NotebookLM |
|---|---|
| Cookie auth (`nlm login --manual`) | ✓ authenticated, validated server-side |
| Adapter lists notebooks | ✓ real notebooks returned |
| Guard passes `notebook create` through | ✓ notebook created live |
| Real Deep Research (fast mode) | ✓ two runs, 10 real sources each |
| **Guard refuses a real duplicate import** | ✓ refused with reason (ledger `0→10`, repeat blocked) |
| Diversity scorer on real sources | ✓ scored, real `type_mix` surfaced |
| `research_assess` fast + deep | ✓ fast "good"/coverage 1.0; deep ablation found 3 redundant pairs, greedy-prune 1/10 |
| `catalog_sync` / `catalog_search` | ✓ indexed 2 notebooks / 20 sources; FTS search returned real hits |
| **`research_fix_videos` (plan + apply)** | ✓ detected 2 blocked bot-wall videos, re-added natively; transcript went 21k junk → **144k real transcript** |

## The empirical findings that matter

### 1. Deep Research is heavily blog-weighted — the kind-diversity signal is justified
Two real runs, same account, fast mode:

| Question | Real `type_mix` | type_diversity |
|---|---|---|
| "how transformers attention works" | `{web:7, paper:2, docs:1}` | 0.33 |
| "how to get started building a RAG app **step by step**" | `{web:6, discussion:1, video:2, paper:1}` | 0.50 |

**Question phrasing changes the kind mix.** The "step by step / get started" framing pulled in
videos and a Reddit thread; the plain conceptual question returned almost all blogs/papers. This is
direct live evidence for the `project_kickstart` thesis: a diverse library is achievable, but you
have to *ask for it*, and the kind-diversity number is what tells you whether you got it.

### 2. "Video" sources from Deep Research are often empty bot-wall shells
Both YouTube sources in run 2 were imported as `"Sign in to confirm you're not a bot"` pages —
`blocked_page: true`, no transcript (21k of navigation junk). `research_fix_videos --apply` repaired
them to real 144k transcripts. **So the raw diversity count over-credits videos that are actually
empty** until repair runs.

### 3. The scorer can't tell a blocked video from a real one (refinement opportunity)
A 21k bot-wall shell and a 144k real transcript both score `depth=1.0` and both count as a `video`
kind toward `type_diversity`. The scorer rewards *length and kind*, not *substance* — so a notebook
full of blocked videos looks deep and diverse while being empty.

## Prioritized refinement proposals (evidence-backed)

1. **Blocked-content awareness in scoring (high value, from finding #3).**
   Teach `quality.py` to detect bot-wall/sign-in shells (a cheap content heuristic:
   high ratio of nav tokens like "sign in", "skip navigation", "search with your voice";
   or a `blocked_page` flag the adapter can already compute via the same logic
   `fix_videos` uses). A blocked source should score low depth and NOT earn full
   kind-diversity credit. This stops empty videos from inflating the headline number.

2. **Wire `research_fix_videos` into the assess/score flow as a warning (medium).**
   `research_score_notebook` / `research_assess` should surface "N video sources look
   blocked — run research_fix_videos" in their output, since live data shows this is the
   common case, not the exception. Today the user only learns it if they run fix_videos
   separately.

3. **A "diversity gap" hint in the score output (medium, from finding #1).**
   When `type_mix` is lopsided (e.g. all web/paper, no video/discussion), emit an
   actionable suggestion ("mostly blogs — add a step-by-step video or a community thread").
   The data shows phrasing drives the mix, so the tool can nudge the next query.

4. **Document the phrasing effect in the research-loop skill (low, cheap).**
   Record the live observation that "step by step / get started" phrasing pulls a more
   diverse kind mix, so the skill can advise it for kickstart research.

## Notes
- All testing used a throwaway account; both test notebooks were deleted, the cookie file
  removed, and the `nlm` profile cleared at the end.
- Reproduce: `nlm login --manual --file <cookies>`, then drive the tools in `notelab._tools`
  or the `nlmx` wrapper directly. The engine/scorer paths need no account.
