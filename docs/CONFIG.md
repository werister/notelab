# Configuration — quality weight profiles

The source-quality score is a weighted sum of five signals. **The weights are config,
not code** — you tune what "good sources" means for the kind of research you're doing,
without forking. Pass a preset name or a dict of overrides as `weights` to
`research_run_experiment`, `research_score_notebook`, or `research_assess`.
(`research_evolve_notebook` takes no `weights` — it scores with the default profile.)

## The five signals

| Signal | Rewards |
|---|---|
| `authority` | primary sources, papers, gov/edu, official docs; penalizes content farms |
| `diversity` | distinct domains/authors (domain entropy) — not 8 copies of one blog |
| `recency` | fresher sources (exponential decay, `recency_halflife_days`) |
| `depth` | substantial content vs. thin stubs (`depth_target_chars`) |
| `relevance` | overlap of source text with the question's terms |

## Presets (`notelab.research.quality.PRESETS`)

| Preset | authority | recency | diversity | depth | relevance | half-life | Use for |
|---|---|---|---|---|---|---|---|
| `news` | 0.30 | **0.35** | 0.15 | 0.05 | 0.15 | 30 d | current events — fresh + authoritative wins |
| `lit_review` | 0.30 | 0.00 | 0.25 | **0.30** | 0.15 | 5 y | surveys — depth + breadth, recency irrelevant |
| `docs` | **0.35** | 0.05 | 0.05 | 0.30 | 0.25 | — | API/technical — the canonical doc, not many opinions |
| `balanced` | 0.25 | 0.15 | 0.20 | 0.20 | 0.20 | 1 y | the default |

### Use a preset

```python
from notelab.research.quality import preset, score_sources
report = score_sources(sources, question, weights=preset("news"))
```

From an MCP client, pass the name directly:

```json
{ "name": "research_run_experiment",
  "arguments": { "question": "...", "strategies": [], "weights": "lit_review" } }
```

### Override individual weights

Start from a preset (or the default) and change what you care about — everything is a
plain dataclass field:

```python
from notelab.research.quality import QualityWeights
w = QualityWeights(authority=0.5, recency=0.3, depth=0.1, diversity=0.05, relevance=0.05,
                   recency_halflife_days=14)   # "breaking news, authority matters most"
```

Weights need not sum to 1 — they're normalized internally. The tuning knobs
`recency_halflife_days` (smaller = favor fresher harder) and `depth_target_chars`
(content length that scores ~1.0 on depth) let you shape two signals further.

## Learned per-topic profiles (M3)

`research_learn_topic` writes a **tuned** weight profile per topic into the ledger,
nudged toward the signals the winning sources actually scored high on — so profiles
improve automatically as you run more experiments. Presets are the starting point;
the learned profile is where it ends up.
