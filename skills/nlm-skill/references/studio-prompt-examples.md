# NotebookLM Studio Prompt Examples

**Target tool:** NotebookLM Studio — `focus_prompt`, `custom_prompt`, or `description` fields.

Pair with [studio-prompting-guide.md](studio-prompting-guide.md) for mode selection and decision trees.

**Usage:** Replace `<notebook-id>` and `[placeholders]`. All generation requires `confirm=True` (MCP) or `--confirm` (CLI).

**Which section to use:**
- **Fast track (default):** Use [Minimal prompts](#fast-track-minimal-prompts) — 1–3 sentences
- **Guided / high-stakes / cinematic:** Use [Guided templates](#guided-templates-full-prompts) — full prompts below

**Difficulty note:** Quiz CLI uses `--difficulty 1-5` (3=medium). MCP uses `difficulty="easy"|"medium"|"hard"`.

---

## Fast Track Minimal Prompts

Use these for typical requests. Always include the grounding anchor.

### Audio
```
Deep-dive podcast for a general audience. Focus on [main themes from notebook].
Conversational tone, ~10 minutes. Use only uploaded sources.
```
```python
studio_create(notebook_id="<id>", artifact_type="audio", audio_format="deep_dive",
              audio_length="default", focus_prompt="...", confirm=True)
```

For a regional Spanish accent, set the locale explicitly:

```python
studio_create(notebook_id="<id>", artifact_type="audio", audio_format="deep_dive",
              language="es-419", focus_prompt="...", confirm=True)
```

NotebookLM has been observed producing Spain Spanish for `es`/`es-ES` and
Latin-American Spanish for `es-US`/`es-419`. Prompt wording does not reliably
change the accent.

### Video (explainer/brief)
```
Explainer for [audience]. Cover [2-3 key topics from sources]. Clear and concise.
Use only uploaded sources.
```

### Video (short)
```
60-second overview of [core concept from sources]. Quickly grasp the key idea.
Use only uploaded sources.
```

### Slide deck
```
Presentation for [audience/meeting type]. Structure: [problem → findings → recommendation].
Use only uploaded sources.
```

### Infographic
```
Infographic highlighting [3 key points from sources]. Clean layout.
Use only uploaded sources.
```

### Report (preset — no custom prompt)
```bash
nlm report create <id> --format "Briefing Doc" --confirm
nlm report create <id> --format "Study Guide" --confirm
nlm report create <id> --format "Blog Post" --confirm
```

### Quiz
```
Multiple-choice quiz on [TOPIC]. Mix definition and scenario questions.
Emphasize [topics from sources]. Use only uploaded sources.
```
```python
studio_create(notebook_id="<id>", artifact_type="quiz", difficulty="medium",
              focus_prompt="...", confirm=True)
```

### Flashcards
```
Flashcards on [TOPIC]. Mix definitions and application scenarios.
Use only uploaded sources.
```

### Data table
```
Extract [entity type] from sources. Columns: [Col1], [Col2], [Col3].
One row per [entity]. N/A if not in sources.
```

### Fast track inference table

| User says | Inferred call | Minimal focus source |
|-----------|---------------|----------------------|
| "Make a podcast" | audio, deep_dive, default | notebook title → `notebook_describe` |
| "Quick audio summary" | audio, brief, short | main themes |
| "Slides for my meeting" | slide_deck, presenter_slides, short | meeting purpose |
| "Shareable slide doc" | slide_deck, detailed_deck, default | notebook themes |
| "LinkedIn infographic" | infographic, square, concise, bento_grid | top 3 takeaways |
| "Explainer video" | video, explainer, auto_select | key topics |
| "Study guide" | report, Study Guide preset | (none needed) |
| "Quiz me" | quiz, medium | notebook themes |
| "Flashcards" | flashcards, medium | key terms |
| "Compare competitors" | data_table | explicit columns in description |

---

## Guided Templates (Full Prompts)

Use for high-stakes deliverables, vague requests, or when user asks to see the prompt. **Cinematic is always guided.**

---

## Audio

### Exam revision (deep_dive, default)
```
Audience: Undergraduate revising for midterm on [TOPIC].
Focus on three commonly confused ideas: [A], [B], [C]. Skip Chapter 1 background.
Structure: intro → one section per idea with example → 2-min exam recap.
Tone: friendly tutors. Use only uploaded sources. ~12 minutes.
```

### Executive brief (brief, short)
```
Single-narrator recap for a team lead before a stakeholder meeting.
Summarize decisions, risks, and open questions only. Skip implementation details.
Under 5 minutes. Use only uploaded sources.
```

### Thesis critique (critique, default)
```
Two expert reviewers of my draft. Identify weakest argument, unsupported claims,
and source disagreements with my thesis. End with top 3 revisions before submission.
Tone: constructive. Use only uploaded sources.
```

### Policy debate (debate, long)
```
Debate [POSITION A] vs [POSITION B] using only uploaded sources.
Each host cites passages. Include common ground and where sources are insufficient.
~18-20 minutes.
```

### ELI12 explainer (deep_dive, default)
```
Explain core concepts as if teaching a smart 12-year-old.
Use analogies for every complex term. Focus on why this matters, not just definitions.
Use only uploaded sources.
```

---

## Video

### Executive briefing (brief, whiteboard)
```
Explain for non-technical executives in under 3 minutes.
Focus: business impact, top 3 risks, measurable upside. Skip jargon.
Use only uploaded sources.
```
```bash
nlm video create <id> --format brief --style whiteboard --focus "..." --confirm
```

### Cross-source explainer (explainer, custom style)
**Style prompt:** `Clean editorial layout, black typography, yellow accents, minimal animation.`
**Focus:** `Connect sources: Problem → Evidence → Implications → Actions. Audience knows [DOMAIN X]. Use only uploaded sources.`
```python
studio_create(notebook_id="<id>", artifact_type="video", video_format="explainer",
              visual_style="custom", video_style_prompt="Clean editorial...",
              focus_prompt="Connect sources: Problem → ...", confirm=True)
```

### Student chapter (explainer, anime)
```
I know nothing about this topic. Focus Chapter [N] only.
Help me read diagrams and key definitions. One example per concept.
Use only uploaded sources.
```

### Product launch cinematic (always guided)
```
I'm the PM who wrote this PRD. Presenting to engineering at sprint planning.

Deliver as a 1980s game show "Ship It Live."
Beats: problem reveal → feature graphics → KPI scoreboard → rollout finale.
Tone: enthusiastic. ~2 minutes.
Visual style: neon set, chunky typography, studio spotlights, orchestral stings.
Use only uploaded sources.
```
```bash
nlm video create <id> --format cinematic --focus "..." --confirm
```

### LinkedIn teaser (cinematic, always guided)
```
2-minute teaser for general professionals. Top 3 surprising findings and why each matters.
Prestige documentary tone. Dark background, slow push-ins on stats, calm narration.
Use only uploaded sources.
```

---

## Slide Deck

### Board briefing (detailed_deck, default)
```
Board-ready deck for C-suite. Structure: situation → 3 findings → financial impact → actions → risks.
Professional tone. Navy background, white text, blue KPI accents. Use only uploaded sources. Skip implementation.
```
```python
studio_create(notebook_id="<id>", artifact_type="slide_deck", slide_format="detailed_deck",
              slide_length="default", focus_prompt="...", confirm=True)
```

### Live sales pitch (presenter_slides, short)
```
20-minute prospect meeting. One idea per slide. Headlines under 8 words.
Structure: pain → solution → proof → pricing → CTA. Icon-led, high contrast.
Use only uploaded sources.
```

### Teaching deck (detailed_deck, default)
```
Teaching deck for visual learners. Hook with surprising stat → 3 core concepts (diagram each) → case → summary.
White background, infographic-style charts. Source-grounded metaphors. Use only uploaded sources.
```

### Revise after generation (on user request or failure only)
```python
studio_revise(notebook_id="<id>", artifact_id="<deck-id>", confirm=True,
              slide_instructions=[
                  {"slide": 4, "instruction": "Update revenue chart from Table 2; add source footnote."},
                  {"slide": 7, "instruction": "Max 3 bullets; enlarge headline."}
              ])
```
```bash
nlm slides revise <artifact-id> --slide '4 Update chart from Table 2' --confirm
```

---

## Infographic

### Executive metrics (landscape, standard, professional)
```
Act as data designer for C-level execs. Z-pattern: headline metric → 3 KPI blocks → trend → conclusion.
Numbers from sources only. 35% white space. Terms: ARR, churn, NRR exactly as written.
```
```bash
nlm infographic create <id> --orientation landscape --detail standard --style professional --focus "..." --confirm
```

### Product comparison (landscape, standard, bento_grid)
```
Side-by-side comparison, two columns. Icons per feature row.
Highlight pricing and top 3 differentiators from sources only.
```

### LinkedIn carousel (square, concise, bento_grid)
```
Poster-style: large headline, max 5 points. One core message. Square for LinkedIn.
Use only uploaded sources.
```

### HR policy poster (portrait, standard, kawaii)
```
Educational poster: title + 5 rules with icons. Friendly, non-intimidating. One rule per block.
Use only uploaded sources.
```

### Process explainer (landscape, standard, sketch_note)
```
Numbered step diagram with short labels. Horizontal timeline. Hand-drawn educational feel.
Use only uploaded sources.
```

---

## Report

### Literature review (Create Your Own)
```
Draft literature review on [TOPIC] for [AUDIENCE], ~1500 words.
Structure: Intro → 3-5 thematic sections (by idea, not author) → Gaps → Conclusion.
Every claim cited (Author Year). Flag contradictions. Use only uploaded sources.
```
```python
studio_create(notebook_id="<id>", artifact_type="report", report_format="Create Your Own",
              custom_prompt="...", confirm=True)
```

### Contradiction map (Create Your Own)
```
Find every meaningful disagreement across sources. For each: claim, sources per side,
evidence strength, which to trust for [TOPIC]. Sort by importance. Use only uploaded sources.
```

### Executive decision brief (Create Your Own)
```
Competitive comparison: options, tradeoffs, recommendation, risks.
Summary table of key metrics. Mark inferred items as "Inferred." Source-grounded numbers only.
```

### Blog post angle (Create Your Own)
```
Write an accessible blog post for [AUDIENCE] about [TOPIC].
Structure: hook → 3-4 sections with takeaways → actionable close.
Conversational but accurate. Use only uploaded sources.
```

---

## Quiz

Studio quiz generates **multiple choice only**. For short-answer or derivations, use chat or Create Your Own reports.

### Professor-style MC mix
```
Generate multiple-choice questions matching exam style for [COURSE].
Mix: (1) numeric/calculation MC with plausible distractors, (2) comparison "best answer" scenarios,
(3) application scenarios requiring combined concepts. Emphasize [TOPICS]. De-emphasize [TOPICS].
Weight lecture material over textbook-only content. Use only uploaded sources.
```
```bash
nlm quiz create <id> --count 10 --difficulty 3 --focus "..." --confirm
```
```python
studio_create(notebook_id="<id>", artifact_type="quiz", question_count=10,
              difficulty="medium", focus_prompt="...", confirm=True)
```

### Integration testing (MC)
```
MC questions requiring combined concepts from [TOPIC] — not isolated definitions.
Each question tests how [A], [B], and [C] work together. Use only uploaded sources.
```

### Live audience poll (MC)
```
MC questions for live poll. Top 5 takeaways from speaker decks.
Accessible language, one clearly best answer each. Use only uploaded sources.
```

---

## Flashcards

### Mixed exam deck (medium)
```
Mix 4 types: (1) Definition + example, (2) Cause-effect, (3) Comparison A vs B,
(4) Application scenario. Focus on repeated terms and summary sections.
Use only uploaded sources.
```

### Scenario decisions (hard)
```
Scenario-based cards testing decision-making, not definitions.
Real situations — choose the right approach. Use only uploaded sources.
```

### Confused terminology (medium)
```
Cards for terms I confuse: [TERM A vs B vs C]. Front: explain difference in one sentence each.
Use only uploaded sources.
```

### Beginner vocabulary (easy)
```
Definition-focused cards for key terms in [TOPIC]. Include one example per term.
Skip advanced edge cases. Use only uploaded sources.
```

### Certification cram (hard)
```
Focus on hardest exam rules, thresholds, and exception cases from prep materials.
Prioritize application over pure definitions. Use only uploaded sources.
```

---

## Data Table

### Competitor matrix
```
Competitor comparison from sources. Columns: Model, Provider, Price (in/out), Context Window,
Key Strength, Best For, Source URL. One row per model. N/A if not in sources.
```
```bash
nlm data-table create <id> "Competitor comparison. Columns: Model, Provider, Price..." --confirm
```

### Historical events
```
Historical events from readings. Columns: Event Name, Country, Date, Key Figures, Economic Consequences.
One row per event. Chronological order. N/A if not in sources.
```

### Research paper matrix
```
Literature comparison. Columns: Author/Year, Method, Sample Size, Key Finding, Limitations, Relevance to [TOPIC].
One row per paper. N/A if not in sources.
```

### Feature extraction
```
Extract key features from each source. Columns: Feature, Description, Source(s), Limitations Mentioned.
One row per feature. N/A if not in sources.
```

### Glossary table
```
Glossary from sources. Columns: Term, Definition, Example, Source Section, Related Terms.
Alphabetical by term. N/A if not in sources.
```
