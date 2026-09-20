# NotebookLM Studio Prompting Guide

**Target tool:** NotebookLM Studio via MCP `studio_create` / CLI `nlm * create`. Prompts go in `focus_prompt`, `custom_prompt`, or `description` — not chat messages.

**Audience:** AI agents (Claude Code, Cursor, Codex, etc.) invoking Studio on behalf of users.

Research basis: Dec 2025 – Jun 2026 (Google Help, practitioner guides, community workflows).

**Companion file:** [studio-prompt-examples.md](studio-prompt-examples.md) — fast-track minimal prompts + guided templates.

---

## Generation Modes: Fast vs Guided

**Default: fast track.** Reduce friction. Do not run intake questionnaires.

### Fast track (default)

Use when the request is clear enough to infer settings from context.

**Triggers:**
- User names an artifact type: "make a podcast", "create slides", "infographic"
- User gives specifics: format, audience, topic, or style in the same message
- User says: "just do it", "fast", "quick", "don't ask questions"

**NOT fast track — always guided:**
- **`video_format=cinematic`** — quota-limited (~2/day Pro); requires full creative brief + one-line quota note, even if user says "make a cinematic video"

**Behavior:**
1. Silently infer context (see [Silent inference sources](#silent-inference-sources-for-fast-track))
2. Pick format/style from decision tree
3. Craft a **minimal** prompt (1–3 sentences) with grounding anchor
4. One compact line to user, then `studio_create(confirm=True)`

```
Creating a default deep-dive audio (~10 min) on your notebook's main themes. Generating now.
```

### Guided preview (exception only)

Use when quality would suffer without a pause, or the user opts in.

**Triggers:**
- Vague request with no inferable goal ("make something cool")
- High-stakes deliverable (client deck, public infographic, branded report)
- **Any cinematic video request**
- Empty notebook or no usable sources
- User asks: "help me craft this", "walk me through", "show me the prompt first"

**Behavior:**
1. Show settings + full proposed prompt (use templates from examples file — **Guided** section)
2. Offer **one** refinement chance — never more than one clarifying question
3. Generate on approval via `studio_create(confirm=True)`

**Anti-patterns (never do these):**
- Multi-question intake ("Who's the audience? What tone? How long? Which sources?")
- Asking format questions when defaults are obvious
- Full preview on every request regardless of clarity
- Using long guided templates for simple fast-track requests
- Blocking on Drive sync — Google auto-syncs Drive sources; only mention sync if user reports stale content

### Mode override phrases

| Phrase | Mode |
|--------|------|
| "just generate it", "fast", "skip questions" | Force fast track (except cinematic → still guided) |
| "help me craft", "show prompt", "walk me through" | Force guided |
| "cinematic video" | **Always guided** + quota warning |

---

## Agent Behavior Contract

```
User requests Studio artifact
  → Classify: fast track or guided? (cinematic → always guided)
  → If notebook empty: stop, add sources first (only hard blocker)
  → Silently infer context (user message → notebook title → notebook_describe if needed)
  → If 5+ sources and focused topic: pass source_ids (no question — infer from request)
  → Pick format/style from decision tree
  → Craft prompt: minimal (fast) or full (guided) + grounding anchor
  → Fast: one-line notice → studio_create(confirm=True)
  → Guided: show settings + prompt → optional refine → studio_create(confirm=True)
  → Poll studio_status until completed or failed
  → Iterate ONLY if: status=failed, user dissatisfied, or slides need studio_revise
```

### Fast track vs `confirm=True`

Fast track reduces **clarifying questions**, not the MCP/CLI confirmation gate.

- **Infer silently** → one-line notice → call `studio_create(confirm=True)` when the user's request constitutes approval to generate
- If the client requires an explicit confirmation step, that single confirm is the **only** pause — do not add intake questions before it
- Never call `studio_create(confirm=True)` without the user having requested generation

### Silent inference sources for fast track

Use in order — **call without asking the user**:

| Priority | Source | When |
|----------|--------|------|
| 1 | User's message | Always |
| 2 | Notebook title | Generic requests ("make a podcast") |
| 3 | `notebook_describe` | Title alone is insufficient; call silently |
| 4 | Prior conversation context | User referenced topics earlier in session |
| 5 | `source_ids` scoping | User named a chapter, source, or topic matching source titles |

**Two prompt layers:**
- **Notebook persona** (`chat_configure` with `goal=custom`) — shapes all Studio outputs; set once for repeat/branded work
- **Per-artifact prompt** — `focus_prompt`, `custom_prompt`, or `description`

**Retrieve successful prompts:** `studio_status` returns `custom_instructions` per artifact for reuse.

---

## Universal Prompt Framework

### Grounding anchor (include in EVERY prompt)

```
Use only uploaded sources. Do not invent statistics, quotes, names, or examples not in the sources.
```

This is the single highest-leverage line for NotebookLM Studio quality.

### Five-block anatomy

| Block | Purpose | Example fragment |
|-------|---------|------------------|
| **Audience** | Who consumes this | "Non-technical executives" |
| **Goal** | One outcome | "Prep for midterm on thermodynamics" |
| **Scope** | Include / exclude | "Chapter 4 only; skip historical background" |
| **Structure** | Sections, beats, layout | "Problem → 3 findings → recommendation" |
| **Constraints** | Tone, length, language | "Conversational; ~10 min; English only" |

**Fast-track minimal (1–3 sentences):**
```
[Audience]. Focus on [2-3 themes from sources]. [Structure or tone hint]. Use only uploaded sources.
```

**Guided full skeleton:**
```
Audience: [who]. Goal: [outcome]. Focus: [themes]. Exclude: [skip].
Structure: [sections]. Tone: [register]. Use only uploaded sources.
```

**Universal anti-patterns:**
- Blank generate with no prompt — always craft at least a minimal focus prompt
- Long Sabrina-style prompts on fast-track requests — save for guided/high-stakes
- "Make it good" / "make it look cool" — infer specifics or switch to guided mode
- All sources enabled on large notebooks — scope with `source_ids` when topic is narrow
- Expecting inline citations in audio/video — verify facts via chat separately
- Regenerating entire deck for one bad slide — use `studio_revise` for slides

---

## Success Criteria (per artifact)

Use to validate output without bothering the user.

| Artifact | Pass | Fail → action |
|----------|------|---------------|
| **Audio** | `studio_status=completed`; topic matches request | Regenerate with tighter scope or `source_ids` |
| **Video** | Completed; explainer/brief matches audience | Refine focus; cinematic: expect ~50% of beats honored |
| **Slides** | Narrative matches requested structure | `studio_revise` for fact/layout fixes; regen if wrong arc |
| **Infographic** | Key stats match sources (spot-check via chat) | Regen with explicit columns/layout; avoid `detailed` level |
| **Report** | Structure and claims source-grounded | Use Create Your Own with contradiction rules |
| **Quiz** | MC questions on requested topics | Refine `focus_prompt`; non-MC needs chat, not Studio |
| **Flashcards** | Cards match requested type mix | Refine focus; export CSV if needed |
| **Data table** | All columns present; N/A not invented numbers | Tighten column schema in `description` |

**Do not offer iteration proactively on success.** Only iterate when status=failed, user is dissatisfied, or slides need targeted `studio_revise`.

---

## Pre-Generation Checklist (Moderate)

Run mentally; do not present as a questionnaire.

| Check | Action if failed |
|-------|------------------|
| Notebook has sources | Add sources first — hard stop |
| Topic is narrow + many sources | Pass `source_ids` without asking |
| Non-English content | Set `language` (BCP-47) |
| Cinematic video | **Always guided** + one-line quota note |
| Repeat/branded work | Consider `chat_configure` — optional, not blocking |

**Skip:** manual Drive stale/sync checks (auto-sync handles this).

---

## Audio (`artifact_type=audio`)

**Parameters:** `audio_format`, `audio_length`, `focus_prompt`

### Language and accent

Use a regional BCP-47 locale when the requested accent matters. NotebookLM has
been observed producing Spain Spanish for `es`/`es-ES` and Latin-American
Spanish for `es-US`/`es-419`. Prompt instructions do not reliably change the
voice accent. `NOTEBOOKLM_HL` sets the default locale, while `language` can
override it per generation. This behavior is controlled upstream and may
change.

### Format × length decision tree

| Scenario | Format | Length |
|----------|--------|--------|
| Quick gist / pre-meeting | `brief` | `short` |
| Standard learning / commute | `deep_dive` | `default` (~10 min) |
| Deep unit review | `deep_dive` | `long` (~20 min) |
| Review your own writing | `critique` | `default` |
| Tradeoffs / exam defense prep | `debate` | `default` or `long` |

**Fast-track prompt:** audience + 2–3 themes + grounding anchor.

**Anti-patterns:** expecting 30+ min from prompt alone; critique/debate without the draft in sources.

---

## Video (`artifact_type=video`)

**Parameters:** `video_format`, `visual_style`, `focus_prompt`, `video_style_prompt` (CLI: `--style-prompt`)

### Format decision tree

| Scenario | Format | Mode |
|----------|--------|------|
| Exec recap (~2 min) | `brief` | Fast track OK |
| Structured learning, data walkthrough | `explainer` | Fast track OK |
| Narrative launch, memorable teaser | `cinematic` | **Always guided** |
| Quick mobile/social recap (~60s, vertical) | `short` | Fast track OK |

### Visual style (explainer/brief only)

| Style | Best for |
|-------|----------|
| `auto_select` | Internal drafts, fast track default |
| `whiteboard` | Tutorials, step-by-step |
| `classic` | General business |
| `watercolor` / `heritage` | Reflective, humanities |
| `kawaii` / `anime` | Playful, youth content |
| Custom (`visual_style` + style prompt) | Brand/public-facing — prefer guided |

### Cinematic: always guided

No visual style picker. Full creative brief in `focus_prompt`. CLI `--style-prompt` remaps into `focus_prompt`.

**Anatomy:** role + audience + genre metaphor → segmented beats (a.k.a. source elements) → tone → visual style paragraph (8–12 comma-separated phrases).

**Quota note (one line):** "Cinematic uses daily quota (~2/day Pro) — proceeding with full brief."

**Anti-patterns:** using `--style` on cinematic; 20+ unfocused sources; expecting exact shot list compliance.

### Short: new, fast-track eligible

A ~60-second, vertical "bite-sized overview" — like Cinematic, it has no visual style picker, so CLI `--style-prompt` remaps into `focus_prompt`. Language selection is best-effort: non-English `--language` values add an explicit requirement covering narration, subtitles, and on-screen text because the captured RPC language slot is null. If NotebookLM rejects the request, the account/region may not have access yet.

**Fast-track prompt:** 1–2 sentences naming the core concept to distill — shorter than explainer/brief prompts since the output itself is short.

---

## Slide Deck (`artifact_type=slide_deck`)

**Parameters:** `slide_format`, `slide_length`, `focus_prompt`

| Scenario | Format | Length |
|----------|--------|--------|
| Email/share without presenter | `detailed_deck` | `default` |
| Live talk / meeting | `presenter_slides` | `short` or `default` |
| Quick exec update | either | `short` |

**Fast-track prompt:** audience + structure arc + grounding anchor.

**Iteration (`studio_revise`) — only when user wants fixes:**
1. Fact fixes per slide **first** (revise does not re-read sources)
2. Layout/cosmetic revisions
3. Export PPTX; budget human polish for image-layer slides

**Anti-patterns:** `presenter_slides` for handouts; revising to add new source data (regenerate instead).

---

## Infographic (`artifact_type=infographic`)

**Parameters:** `orientation`, `detail_level`, `infographic_style`, `focus_prompt`

### Orientation × detail × style matrix

| Goal | Orientation | Detail | Style start |
|------|-------------|--------|-------------|
| Slide embed / business | `landscape` | `standard` | `professional`, `scientific` |
| LinkedIn / social | `square` | `concise` | `bento_grid`, `professional` |
| Mobile / poster | `portrait` | `concise` or `standard` | `editorial`, `kawaii` |
| Process / how-to | `landscape` | `standard` | `instructional`, `sketch_note` |
| Internal draft | any | `standard` | `auto_select` |

**Style guide (11 presets):** `sketch_note` (workshops), `professional` (B2B), `bento_grid` (comparisons), `editorial` (thought leadership), `instructional` (SOPs), `bricks`/`clay` (chunked / kid-friendly), `anime`/`kawaii` (informal), `scientific` (research).

**Fast-track prompt:** layout type + 3 key points + grounding anchor.

**Anti-patterns:** `detailed` detail level as default; `auto_select` for client deliverables.

---

## Report (`artifact_type=report`)

**Parameters:** `report_format`, `custom_prompt` (required for Create Your Own)

| Scenario | Format | Mode |
|----------|--------|------|
| Meeting prep, single-source skim | `Briefing Doc` | Fast track (no custom prompt) |
| Exam prep with built-in Q&A | `Study Guide` | Fast track |
| Shareable narrative | `Blog Post` | Fast track |
| Lit review, contradiction map, custom structure | `Create Your Own` | Guided |

**Anti-patterns:** Briefing Doc when source disagreements must be visible; vague custom prompts.

---

## Quiz (`artifact_type=quiz`)

**Parameters:** `question_count`, `difficulty`, `focus_prompt`

**Studio quiz = multiple choice only.** Short-answer, derivations, and essays require chat or Create Your Own reports — not `artifact_type=quiz`.

| Scenario | Settings |
|----------|----------|
| Warm-up / vocab | easy, fewer questions |
| Regular study | medium, default count |
| Pre-exam stress test | hard, more questions |
| Topic/style steering | custom `focus_prompt` (MC formats only) |

**CLI note:** `--difficulty` is 1–5. **MCP:** `easy` / `medium` / `hard`.

**Anti-patterns:** prompt asking for non-MC question types; "make it hard" without topic scope.

---

## Flashcards (`artifact_type=flashcards`)

**Parameters:** `difficulty`, `focus_prompt`

**Fast-track prompt:** card type (definition vs scenario) + topic scope + grounding anchor.

**Anti-patterns:** default "What is X?" deck for application exams; no scope on large notebooks.

---

## Data Table (`artifact_type=data_table`)

**Parameters:** `description` (REQUIRED)

**Fast-track still requires explicit columns in `description`:**
```
[Purpose]. Columns: Col1, Col2, Col3. One row per [entity]. N/A if not in sources.
```

**Anti-patterns:** "important info table" without columns; 70+ page pattern extraction (chat may work better).

---

## Cross-Artifact Workflows

| Pipeline | Steps |
|----------|-------|
| **Audio escalation** | brief → deep_dive → critique → debate (same sources) |
| **Exam stack** | Study Guide → quiz → flashcards |
| **Research to visual** | deep research import → slides → infographic |
| **Messy PDF cleanup** | audio on PDF → re-add as source → infographic |
| **Slide polish** | generate → `studio_revise` (facts) → export PPTX |

---

## Iteration & Recovery

| When | Action |
|------|--------|
| `studio_status=failed` | Report error; suggest narrower sources or refined prompt |
| User dissatisfied | Offer one regen with refined prompt or slide revise |
| Slides need fixes | `studio_revise` — one change class per instruction |
| Cinematic near-miss | Download → re-upload as source → regenerate (unofficial) |
| Success | Done — do not proactively suggest iteration |

Reuse prompts via `custom_instructions` in `studio_status` output.

---

## Key Sources

| Date | Source |
|------|--------|
| Mar 2026 | [Google Cinematic Video](https://blog.google/innovation-and-ai/products/notebooklm/generate-your-own-cinematic-video-overviews-in-notebooklm/) |
| Dec 2025 | [Google Slide Decks Guide](https://blog.google/innovation-and-ai/models-and-research/google-labs/8-ways-to-make-the-most-out-of-slide-decks-in-notebooklm/) |
| Mar 2026 | [Jeff Su — NotebookLM 2026](https://www.jeffsu.org/notebooklm-changed-completely-heres-what-matters-in-2026/) |
| Apr 2026 | [MakeUseOf — Audio Prompts](https://www.makeuseof.com/notebooklm-audio-overviews-better-custom-prompt/) |
| Mar 2026 | [Nova Express — Infographics](https://blog.novaexpress.ai/2026/03/25/notebooklm-infographic-the-complete-guide-to-turning-your-data-into-visual-stories/) |
| May 2026 | [The AI Thinker — Cinematic Briefs](https://www.theaithinker.com/p/how-to-turn-work-into-cinematic-videos) |
| Ongoing | [Google Help — Audio](https://support.google.com/notebooklm/answer/16212820), [Video](https://support.google.com/notebooklm/answer/16454555), [Reports/Data](https://support.google.com/notebooklm/answer/16206563) |
