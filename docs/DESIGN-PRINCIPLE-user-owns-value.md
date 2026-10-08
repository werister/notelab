# Design principle: the user owns what they value

> The user of the repo is in charge of what they value — academic sources, YouTube
> videos, forums — combined, or weighted differently depending on the use case.

This is a first-class design constraint for NoteLab's scoring, not a feature idea.

## What it means

NoteLab must **not** hardcode a single opinion of what a "good" source mix is. The
kind-diversity signal and the authority heuristics are *defaults*, not verdicts. The
engine already treats weights as config (`QualityWeights`, presets); this principle
extends that to **source kinds**:

- A user doing a **literature review** may value papers heavily and want forum/video
  sources discounted or ignored.
- A user **learning a new tool** may value step-by-step YouTube + forum discussion
  *above* papers.
- A user **starting a project** may want a balanced mix (the `project_kickstart` case).

The right model is: the user declares a **per-kind value profile** (and whether
diversity across kinds is even desirable for this task), and the scorer honors it.
"Diverse is better" is one profile among many — the default, not the law.

## Design implications (to realize this)

1. **Per-kind weights / preferences** on `QualityWeights`, e.g.
   `kind_weights = {"paper": 1.0, "video": 0.3, "discussion": 0.0}` — a source's
   contribution scales by its kind'''s value to *this* user. `None`/empty = today'''s
   neutral behavior (no kind is privileged).
2. **A "diversity intent" switch.** Kind-diversity should be rewardable *or*
   irrelevant per task. For a pure lit-review the user may not want a video to raise
   the score at all; for kickstart they do. Make `diversity_type_weight` (already
   present) the knob, and let a `kind_weights` of mostly-zeros express "I only value
   these kinds."
3. **Presets as starting points, not cages.** Ship opinionated presets
   (`lit_review`, `project_kickstart`, `forum_first`, …) but every one is a plain
   dict the user can override.
4. **Transparency stays.** Whatever profile is active, the report still shows
   `type_mix` and per-signal breakdown so the user sees *why* a set scored as it did
   under their own weighting.

## Non-goals

- No fixed global ranking of kinds (e.g. "papers always beat forums"). That'''s the
  opposite of this principle.
- No silent default that privileges academia — the neutral default treats kinds
  evenly on authority and lets the user opt into a value profile.
