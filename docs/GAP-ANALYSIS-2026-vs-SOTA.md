# NoteLab — Gap Analysis vs. State of the Art (late 2026)

> **What this is.** A research/analysis report. No code was changed. It asks: given what
> comparable "deep research" agents and NotebookLM tooling do as of late 2026, what is
> NoteLab *missing*, where is its "score the sources, not the answer" stance a strength vs. a
> limitation, and what concrete additions would help — flagging anything that would conflict
> with the stdlib-only / offline-tests / deterministic / confirm-gated design.
>
> **Grounded in the code.** The current-state claims below were checked against the actual
> engine (`src/notelab/research/quality.py`, `evolve.py`, `semantic.py`, `strategy.py`,
> `_tools.py`) and the docs (`DESIGN.md`, `CONFIG.md`, `SCOPE.md`, `FIELD-NOTES.md`), not just
> the README's description of itself.
>
> **Guiding principle (from the maintainer).** *The user owns what they value* — academic
> papers vs. YouTube vs. forums, combined or per use-case. Scoring must stay **configurable,
> not prescriptive**. Every recommendation below is tested against that principle.

---

## 0. What NoteLab is today (verified against the code)

A self-improving research MCP server over Google NotebookLM. The parts that matter for this
analysis:

- **`nlmx` guard** over the upstream `nlm` CLI — refuses the three+ confirmed corruption
  behaviours (repeat import duplicates sources, `--source-ids` deselects everything, missing
  `--confirm`, `chat start` hang).
- **Deterministic, LLM-free source scorer** (`quality.py`). Five signals, each 0–1, combined
  by a weight vector that is *config, not code*:
  - **authority** — hardcoded high/low domain lists + TLD bumps (`gov/edu/int/mil` → 0.95) +
    a spam-hostname regex; platform kinds (video/repo/discussion) floored to a neutral 0.5;
    per-domain user overrides via `authority_overrides`.
  - **diversity** — a *blend* (`diversity_type_weight`, default 0.5) of domain-entropy and
    **kind-spread** across a fixed 7-kind taxonomy (`paper · docs · repo · video ·
    discussion · reference · web`).
  - **recency** — exponential decay, `recency_halflife_days`.
  - **depth** — saturating log of content length vs. `depth_target_chars`.
  - **relevance** — **purely lexical** binary term-coverage (`covered / len(question_terms)`),
    optionally widened by a hand-written or (gated) Mistral-embedding `term_expansions` map.
- **A/B over prompt strategies** ranked by that composite, recorded to a SQLite ledger.
- **Per-topic strategy memory** (`strategy.py`) — tunes the 5 weights toward what the winning
  sources scored high on; persisted per exact-question string.
- **Self-evolving sources** (`evolve.py`) — pure `plan_evolution()` returning
  create/merge/skip/evict; EVICT is confirm-gated; a coverage guard never evicts the sole
  cover of a question term. Dedup is **single-notebook Jaccard-in-merge only**.
- **Catalog** — SQLite+FTS index of notebooks/sources ("what do we already know about X").
  Index-and-search only; **no cross-notebook synthesis**.
- **Multi-family sparring**, **ablation / source-value**, **skill-trend evals**, a
  **versioned local code index**, **video repair** for YouTube bot-wall imports.

**Confirmed absent across the whole repo** (searched): any citation-grounding /
claim-support / entailment check; any **answer-quality** metric (by design — it scores "what
got retrieved, not the prose"); any cross-notebook synthesis; any scheduler / continuous
research; any export tool (markdown/PDF/BibTeX/CSV). The engine is genuinely stdlib-only and
the default path is 100% offline.

---

## 1. Capabilities comparable tools offer that NoteLab lacks

The honest headline: NoteLab is strong and almost unique at **source-set quality as a
reproducible, token-free number**. It is thin or absent on five things the rest of the 2026
field treats as table stakes.

### 1a. Citation / claim verification — the biggest real gap
Deep-research tools in 2026 are judged heavily on *whether the cited source actually supports
the claim*, because the failure is now measured and large:
- 3–13% of citation **URLs** emitted by deep-research agents are hallucinated (no Wayback
  record, likely never existed), and 5–18% are non-resolving overall; deep-research agents
  emit more citations per query *and* hallucinate URLs at higher rates than plain
  search-augmented LLMs ([arXiv 2604.03173](https://arxiv.org/abs/2604.03173)).
- AI-assisted survey papers show a ~17% "phantom rate" of citations that resolve to no
  digital object ([arXiv 2601.17431](https://arxiv.org/html/2601.17431v1)).
- A whole sub-field now exists for detecting citation hallucination — multi-agent pipelines
  that decompose checking into claim extraction → evidence retrieval → passage matching →
  judgment ([arXiv 2602.23452](https://arxiv.org/html/2602.23452v1)), mechanistic detectors
  ([FACTUM, arXiv 2601.05866](https://arxiv.org/pdf/2601.05866v1)), and
  real-vs-fabricated classifiers ([CiteTracer, arXiv 2605.08583](https://arxiv.org/abs/2605.08583)).

NoteLab scores whether sources *look* good; it never checks whether a source **resolves**,
whether its metadata is faithful, or whether an answer's sentence is actually grounded in the
source it cites. The cheapest, most on-brand slice of this — **dead-link / resolvability
checking** — needs no LLM and no new dependency (see §4).
*(Content rephrased for compliance with licensing restrictions.)*

### 1b. Answer-quality evaluation (vs. source-quality)
The 2026 benchmark literature is almost entirely about **report/answer** quality: DeepResearch
Bench's PhD-level tasks ([ICLR 2026](https://proceedings.iclr.cc/paper_files/paper/2026/file/465f22be10e07b301c6ed58f0472f704-Paper-Conference.pdf)),
DEER's 7-dimension / 101-rubric taxonomy
([arXiv 2512.17776](https://arxiv.org/abs/2512.17776)), and DREAM's warning about the "Mirage
of Synthesis" where fluent prose and aligned citations hide factual/reasoning defects
([ACL 2026](https://aclanthology.org/anthology-files/pdf/acl/2026.acl-long.448.pdf)).
NoteLab deliberately scores only the retrieved set. That is a *defensible* stance (see §3),
but it means NoteLab cannot answer the question every other tool is being graded on: "is the
report any good?" It can be an **optional, token-costing v2 lane** without touching the free core.

### 1c. Multi-notebook / cross-corpus synthesis
Every `research/` operation is single-notebook. The catalog indexes across notebooks but only
*searches*. STORM-style systems build a report by **surveying multiple perspectives and
curating across sources into one outline** ([Stanford STORM, arXiv 2402.14207](https://arxiv.org/html/2402.14207v1));
production agents like [tarun7r/deep-research-agent](https://github.com/tarun7r/deep-research-agent)
synthesize citation-backed reports with per-source credibility scoring across the whole run.
NoteLab has no "score/dedupe the union of notebooks A+B+C" path.

### 1d. Cross-notebook deduplication
Dedup today is Jaccard-at-merge inside one notebook's evolve cycle. The scorer itself never
dedupes before averaging, so near-duplicate sources inflate the per-source means (this is the
same mean-structure issue `FIELD-NOTES` flags for ablation). Nothing detects the *same URL /
same paper* living in two notebooks.

### 1e. Scheduled / continuous research + export
- **Scheduling:** managed deep-agents now ship with "durable memory + a daily schedule"
  ([LangChain](https://docs.langchain.com/langsmith/managed-deep-agents-tutorial)); commercial
  research tools advertise "run manually, schedule, or trigger via API"
  ([researchao](https://researchao.com/)). NoteLab's "decay" is a per-manual-run surrogate for
  staleness, not a scheduler.
- **Export:** NotebookLM wrappers now export Audio/Video Overviews, slide decks, mind-map
  JSON, data tables, and Google Docs/Sheets, including batch downloads the web UI doesn't offer
  ([notebooklm-py](https://github.com/teng-lin/notebooklm-py),
  [Apify export actor](https://apify.com/clearpath/notebooklm-api)). NoteLab has **no export
  tool at all** — no way to get a scored source list out as Markdown/CSV/BibTeX, which is odd
  given the whole value prop is "here is a scored, diverse library."

### 1f. NotebookLM Studio surface NoteLab doesn't touch
NotebookLM in 2026 generates Audio/Video Overviews, Mind Maps, Slide Decks, Infographics,
Quizzes, Flashcards, and ships Deep Research + a Gemini-3 engine, with a programmatic Notebook
API ([felloai feature history](https://felloai.com/notebooklm-update-1m-token-chat-goals-saved-history/),
[DigitalOcean overview](https://www.digitalocean.com/resources/articles/what-is-notebooklm),
[useapi Notebook API walkthrough](https://useapi.net/docs/articles/gemini-notebook-bash)).
NoteLab's `nlmx` only guards four behaviours and passes the rest through; it has no notion of
these artifacts. Mind-map JSON in particular is a *free, structured* diversity/coverage signal
NoteLab currently ignores. (Scope-wise, generating artifacts is arguably out of NoteLab's lane —
but *reading* a mind map to inform coverage is squarely in it.)

---

## 2. "User owns what they value" — UX/config patterns NoteLab could adopt

**The honest finding:** NoteLab only *partially* delivers its own principle today. The user
owns the **weighting blend** (5 signals, presets, per-domain `authority_overrides`,
`term_expansions`, `diversity_type_weight`). But the **source-kind dimension** — the exact
thing the maintainer named ("academic vs YouTube vs forums, combined or per use-case") — is
**not** directly expressible:

- The 7-kind taxonomy is **hardcoded** (`SOURCE_TYPES`); a user cannot add `podcast`, `course`,
  `newsletter`, `dataset`.
- There is **no per-kind weight** — you cannot say "value video 0.4, forums 0.3, papers 0.2."
  Diversity treats all kinds as *equally desirable to span*.
- There is **no kind allow/deny-list** — no way to say "exclude YouTube entirely for this
  question" or "papers only." The only proxies are blunt and wrong-shaped (down-rank specific
  domains via `authority_overrides`, or zero out diversity).

What comparable tools let users do, that maps cleanly onto this principle:

- **Domain include/exclude lists.** Perplexity lets you pass up to 20 include/exclude domains
  (by domain, TLD, or domain part) per request
  ([Perplexity domain filter](https://docs.perplexity.ai/guides/search-domain-filters)), and
  offers a one-click **academic mode** that restricts to arxiv/nature/.edu
  ([academic search](https://docs.perplexity.ai/docs/cookbook/articles/academic-search/README)).
- **Source trust labels.** Perplexity surfaces Government / Academic / Trusted labels on cited
  domains so the *user* sees why a source ranked
  ([source labels](https://www.perplexity.ai/help-center/en/articles/20260806-understanding-source-labels)).
  NoteLab already computes an authority tier internally — it just never exposes "why this
  source scored what it did" as a per-source label.
- **Mode presets as first-class.** Elicit vs. Consensus differ precisely by *what the user
  values* — systematic-review extraction vs. fast claim-support evidence
  ([comparison](https://paperguide.ai/blog/elicit-vs-consensus/)). NoteLab's presets
  (`lit_review`, `news`, `docs`, `project_kickstart`) are the same idea and are a genuine
  strength — but they're buried as a `weights` arg rather than a visible "research mode."
- **Recency controls** as an explicit user dial (hour/day/week/month/year), common across
  Perplexity-style APIs ([onekey-gateway docs](https://github.com/aiagenta2z/onekey-gateway/blob/main/docs/onekey-gateway/perplexity/README.md)).
  NoteLab has `recency_halflife_days` but it's a weight-knob, not a user-facing "only last 12
  months" filter.

**Adoptable, and fully in keeping with the stdlib/deterministic design** (detail in §4):
`QualityWeights.kind_weights: dict[str,float]` (per-kind preference), a
`kind_allow` / `kind_deny` list, a user-extensible `kind_rules` map (domain → kind, so a user
can teach it `podcast`), and a **per-source "why" breakdown** in every report. None needs an
LLM, a network call, or a new dependency. This is the single highest-leverage area because it
is literally the maintainer's stated principle, currently under-delivered.

---

## 3. "Source quality, not answer quality" — strength vs. limitation

**Where it's a genuine strength (keep it, loudly):**
- **Reproducible & auditable.** Same input → same score, every number decomposes into five
  signals. The 2026 answer-quality benchmarks are explicitly fighting the "Mirage of Synthesis"
  (fluent, well-cited, still wrong — [DREAM](https://aclanthology.org/anthology-files/pdf/acl/2026.acl-long.448.pdf)).
  A deterministic *source-set* score sidesteps that failure mode entirely.
- **Token-free & offline.** No key, no spend, no supply chain. Nothing else in the field
  offers a quality number for $0.
- **Right unit for curation.** For "did I read widely enough before building?", the source
  *set* is the correct object, and kind-diversity is a better signal than any single answer.

**Where it's a limitation:**
- It says nothing about whether the retrieved sources were actually *used correctly*, or
  whether a confident answer is grounded (§1a/§1b). A notebook can score 0.9 on sources and
  still produce a hallucinated synthesis.
- **Relevance is lexical.** A perfectly on-topic source that uses synonyms ("surface codes /
  fault tolerance" for "quantum error correction") scores 0.0 on relevance (weighted 0.20).
  This is documented and pinned, but it systematically under-scores real sources. The semantic
  path exists but is gated behind a Mistral key and *still* only feeds binary coverage.
- **Authority can't recognize a question's own canonical docs** (Wikipedia 0.95 > official
  FastAPI docs 0.5) — `FIELD-NOTES` gap #10.
- **Looks-good ≠ resolves/supports.** With 3–18% of deep-research citations dead or fabricated
  ([arXiv 2604.03173](https://arxiv.org/abs/2604.03173)), a scorer that never dereferences a
  URL can rank a dead link highly.

**How to close the gap without betraying the LLM-free core:**
1. **Resolvability as a deterministic signal.** A HEAD/GET via stdlib `urllib` tells you if a
   source is a dead link or a bot-wall (NoteLab *already* detects bot-walls for video repair —
   generalize it). No LLM. Keep it opt-in/offline-skippable so CI stays network-free.
2. **Make relevance semantic *within the deterministic contract*.** BM25-lite with IDF over the
   notebook corpus is still pure stdlib and deterministic, and fixes much of the synonym
   under-scoring without a key. Keep embeddings as the opt-in upgrade.
3. **Keep answer-quality strictly as an opt-in, token-costing lane** layered *on top of* the
   free score — never a replacement — exactly as `DESIGN.md` already positions "v2 LLM-judged
   answer quality." Gate it like `semantic.py` (explicit opt-in, hard error if the key is
   missing, nothing in the core imports it).

The core stance is *correct for its unit of analysis*. The fix is not to abandon source-quality
— it's to (a) stop over-trusting a URL that may not resolve, and (b) stop under-scoring
synonym-rich sources, both achievable deterministically.

---

## 4. Prioritized, concrete additions

Effort: **S** ≈ hours, **M** ≈ a day or two, **L** ≈ multi-day. Every item notes design fit.

| # | Addition | One-line rationale | Effort | Design fit |
|---|---|---|---|---|
| **P0-1** | **Per-kind weights + kind allow/deny on `QualityWeights`** (`kind_weights`, `kind_allow`, `kind_deny`) | Directly delivers the maintainer's "user owns what they value" principle, which is only half-delivered today | **M** | ✅ stdlib, deterministic, pure config |
| **P0-2** | **User-extensible kind taxonomy** (`kind_rules: domain→kind`, allow new kinds like `podcast`/`course`) | Lets the user define *their* source universe instead of a fixed 7 kinds | **S–M** | ✅ pure config; taxonomy stops being hardcoded |
| **P0-3** | **Per-source "why" breakdown in every report** (each source's authority/kind/depth/recency/relevance + the tier label) | Transparency the principle implies; mirrors Perplexity's source labels; aids debugging misclassification | **S** | ✅ already computed internally, just expose it |
| **P1-1** | **Dead-link / resolvability signal** via stdlib `urllib` HEAD, reusing bot-wall detection | 3–18% of deep-research citations are dead/fabricated; a looks-good scorer shouldn't rank a dead URL high | **M** | ✅ no LLM/dep; must be opt-in so offline CI stays green |
| **P1-2** | **BM25-lite (IDF-weighted) relevance** to replace binary lexical coverage | Fixes systematic synonym under-scoring *without* a key, staying deterministic | **M** | ✅ stdlib; keep embeddings as the opt-in upper tier |
| **P1-3** | **Export tool** (`research_export`) → Markdown / CSV / BibTeX of the scored source set | The whole value prop is "a scored library"; today you can't get it out of the tool | **S–M** | ✅ pure stdlib serialization |
| **P1-4** | **Cross-notebook dedup + union scoring** (same-URL / same-paper across notebooks; score the union) | Catalog already indexes across notebooks; scoring/dedup across them is the missing half | **M** | ✅ reuse evolve's Jaccard + catalog index |
| **P2-1** | **Authority calibration: question-canonical-docs boost** | Fixes the documented "Wikipedia 0.95 > official docs 0.5" miscalibration (`FIELD-NOTES` #10) | **S** | ✅ heuristic/config, deterministic |
| **P2-2** | **Mind-map JSON as a coverage signal** (read NotebookLM's mind map, not generate it) | A free structured breadth/coverage signal NoteLab currently ignores | **M** | ⚠️ depends on `nlm`/API exposing it; read-only, still deterministic |
| **P2-3** | **Scheduled / recurring research hook** (emit a re-run plan + staleness report; let the user's cron drive it) | Continuous research is now standard; NoteLab's decay is only a per-run surrogate | **M** | ⚠️ keep it as a *plan emitter*, not a daemon, to preserve local-first/confirm-gated design |
| **P3-1** | **Opt-in LLM answer-quality lane** (grounding/claim-support), gated exactly like `semantic.py` | Lets NoteLab speak to the metric the rest of the field is graded on, without touching the free core | **L** | ⚠️ must stay opt-in, key-gated, never imported by core; token cost |
| **P3-2** | **Opt-in citation-support check** (does answer sentence X trace to source Y) | Addresses the measured citation-hallucination problem for users who want it | **L** | ⚠️ LLM/NLI needed; opt-in lane only |

**Recommended first slice (one coherent, low-risk PR):** P0-1 + P0-2 + P0-3. It fully realizes
the maintainer's stated principle, is pure config + exposure of already-computed numbers,
stays stdlib-only and deterministic, needs no network, and keeps every existing offline test
green. P1-1 (dead-link) and P1-3 (export) are the next-best wins and equally low-risk.

### Design-conflict flags (be honest)
- **Anything LLM/embedding-based (P1-2 embeddings tier, P3-1, P3-2)** must follow the
  `semantic.py` pattern: opt-in, key-gated, hard error on missing key, **never imported by the
  core**, and never counted in the free composite. Otherwise it betrays "stdlib-only core / $0
  by default."
- **Network signals (P1-1 dead-link, P2-2 mind map)** must be skippable so the offline test
  suite and `scripts/verify.sh` stay network-free and green. Pin them with mocked HTTP contract
  tests (as `test_semantic_expansions.py` already does), plus one env-gated live test.
- **Scheduling (P2-3)** must stay a *plan/report emitter*, not a background daemon — a daemon
  would break the local-first, single-account, confirm-gated model and the "every destructive
  action passes the confirm gate" guarantee.
- **Per-kind weights (P0-1)** interact with the existing `diversity` signal — spec it so
  per-kind preference and kind-diversity compose predictably (e.g. kind weights shape the
  *scored contribution*, diversity still rewards spread), and re-score historical ledger
  entries behind a version flag so old scores aren't silently redefined (the same discipline
  the repo already applied to the lexical-relevance limitation).

---

## Sources
- Deep-research reference/URL hallucination rates — [arXiv 2604.03173](https://arxiv.org/abs/2604.03173), [arXiv 2601.17431 (17% phantom)](https://arxiv.org/html/2601.17431v1), [arXiv 2605.07723](https://arxiv.org/abs/2605.07723)
- Citation-support verification pipelines — [arXiv 2602.23452](https://arxiv.org/html/2602.23452v1), [FACTUM 2601.05866](https://arxiv.org/pdf/2601.05866v1), [CiteTracer 2605.08583](https://arxiv.org/abs/2605.08583), [CiteCheck 2605.27700](https://arxiv.org/html/2605.27700v1)
- Answer-quality benchmarks / "Mirage of Synthesis" — [DeepResearch Bench (ICLR 2026)](https://proceedings.iclr.cc/paper_files/paper/2026/file/465f22be10e07b301c6ed58f0472f704-Paper-Conference.pdf), [DEER 2512.17776](https://arxiv.org/abs/2512.17776), [DREAM (ACL 2026)](https://aclanthology.org/anthology-files/pdf/acl/2026.acl-long.448.pdf)
- Multi-perspective synthesis — [Stanford STORM 2402.14207](https://arxiv.org/html/2402.14207v1), [tarun7r/deep-research-agent](https://github.com/tarun7r/deep-research-agent)
- Source-preference UX — [Perplexity domain filter](https://docs.perplexity.ai/guides/search-domain-filters), [Perplexity academic search](https://docs.perplexity.ai/docs/cookbook/articles/academic-search/README), [Perplexity source labels](https://www.perplexity.ai/help-center/en/articles/20260806-understanding-source-labels), [Elicit vs Consensus](https://paperguide.ai/blog/elicit-vs-consensus/)
- NotebookLM 2026 features / API / export — [felloai feature history](https://felloai.com/notebooklm-update-1m-token-chat-goals-saved-history/), [DigitalOcean overview](https://www.digitalocean.com/resources/articles/what-is-notebooklm), [notebooklm-py](https://github.com/teng-lin/notebooklm-py), [Apify export actor](https://apify.com/clearpath/notebooklm-api), [useapi Notebook API](https://useapi.net/docs/articles/gemini-notebook-bash)
- Scheduling / continuous research — [LangChain scheduled deep agent](https://docs.langchain.com/langsmith/managed-deep-agents-tutorial), [researchao](https://researchao.com/)

*Content from external sources was rephrased/summarized for compliance with licensing restrictions; see links for originals.*
