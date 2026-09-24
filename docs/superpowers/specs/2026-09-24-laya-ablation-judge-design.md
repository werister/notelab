# Laya as an ablation tie-breaker

## Problem

`research/ablation.py::ablate_sources()` does leave-one-out ablation: for each source,
recompute the notebook's composite quality score and question-coverage with that source
removed, and classify it `load-bearing` / `redundant` / `marginal` from the resulting deltas.
The `redundant` classification requires `composite_delta <= composite_eps` (a fixed `0.02`)
and no coverage loss.

`composite_delta` is a leave-one-out shift in a **mean** over N sources, so it shrinks
roughly as `1/N` by construction — independent of how different that source actually is from
the rest. Verified two ways this session:

- **Live, on a real 41-source notebook**: every single source came back `redundant`, with
  deltas all under 0.005, despite the notebook's own diversity score (0.49) and per-source
  relevance spread (0.02–0.79) showing real heterogeneity.
- **Offline, synthetic data**: a deliberate 10x content-strength gap between "strong" and
  "weak" sources still collapsed to all-`redundant` at N=3 (deltas -0.019 to 0.009), N=4, and
  N=6. The fixed 0.02 threshold only worked at N=2 (deltas 0.17–0.23).

Full evidence: `docs/FIELD-NOTES.md` gap #8.

This isn't a narrow edge case — it's the common case. Any notebook with more than a couple of
sources (i.e. almost all of them) gets an ablation report where the redundant/load-bearing
distinction is structurally meaningless.

## Non-goals

- Not replacing the deterministic scorer. `quality.py`'s per-source signals (authority, depth,
  recency, relevance, diversity) and their aggregation are unaffected — this only touches the
  *classification* step in `ablate_sources`, not the underlying scores.
- Not touching gap #9 (`research_spar`'s family-routing gap) — unrelated, separate problem.
- Not a general "add an LLM judge everywhere" — scoped to this one, already-diagnosed bug.
- Not changing the stdlib-only default path. `pip install .` must still work with zero
  third-party dependencies, exactly as it does today — this is opt-in, same guarantee
  `research/semantic.py` already makes for its Mistral backend.

## Why Laya, not the cheaper statistical fix gap #8 itself proposed

FIELD-NOTES.md gap #8 already sketches a pure-statistics fix: scale `composite_eps` by
`k/N`, or rank deltas by percentile within the set instead of a fixed absolute threshold. That
fix is real, deterministic, and needs no new dependency — and it's not implemented here, which
deserves an explicit reason rather than silence.

Two honest reasons: (1) a percentile-based ranking still needs a cut line somewhere — "top
quartile of deltas is load-bearing" replaces one arbitrary constant with another, and doesn't
resolve the case where *all* deltas are genuinely close (a real, undifferentiated source set,
not a bug) versus *only appearing* close because N flattened them (the actual bug) — a
percentile scheme can't tell those apart from the deltas alone; a grounded judgment on the
actual content can. (2) this is also a deliberate exploration of local-model decision engines
as a notelab capability (the actual origin of this spec), not purely the cheapest fix for gap
#8 in isolation. Both are true; neither should be hidden behind "Laya was simply the better
engineering choice." **If the statistical fix alone is preferred, that's a smaller, dependency-free
change or a distinct spec** — this document only covers the Laya-based approach.

**Consequence of staying opt-in**: `use_laya` defaults to `False`, so gap #8 is **not fixed by
default** — a caller who doesn't pass `use_laya=True` (or doesn't have `laya-mlx` installed)
gets the exact same broken classification as today. That's a direct tradeoff against the
stdlib-only-default guarantee above, stated plainly rather than implied: this spec makes the
bug *fixable*, not fixed, for the default install.

## Approach

Keep the deterministic leave-one-out computation as-is — it's the right signal, the problem is
only the fixed-threshold classification of it. Add an opt-in tie-breaker for the specific zone
where that classification is unreliable: small `composite_delta` (below `composite_eps`) with
no coverage loss. Today that zone defaults to `redundant` unconditionally; that's the bug. With
`use_laya=True`, that zone instead asks a real typed decision, grounded in the source's own
content and what the remaining sources already cover — a `choice` question, "does removing
this source actually hurt coverage of the question, given what's left" — via Laya (Convai Innovations' open-weight, 421M-parameter,
non-autoregressive decision model — a single forward pass, calibrated probabilities, no text
generation, ~1s per call once the checkpoint is warm on this machine, verified live this
session).

Sources with a clear signal (real coverage loss, or a composite delta that clears the
threshold on its own) never touch Laya — this keeps the common fast path exactly as fast as it
is today, and only spends the ~1s-per-call cost on the specific ambiguous cases the current
code gets wrong anyway.

**This breaks a stated guarantee, explicitly, not by omission.** `ablation.py`'s own module
docstring says "Deterministic + offline: reuses `quality.score_sources`. No network, no keys"
(line 21). `use_laya=True` makes a real network call (checkpoint fetch) on first use and a
real model inference call per ambiguous source — neither deterministic nor offline. The
docstring must be updated to say so once this ships (e.g. "...deterministic and offline by
default; `use_laya=True` opts into a network-backed, non-deterministic tie-breaker for
ambiguous cases — see laya_judge.py"), not left claiming a guarantee that's no longer
universally true. `evolve.py` consumes these verdicts for evict/keep decisions — anyone relying
on ablation being reproducible needs this called out where they'd actually read it, not just
in this spec.

## Components

### `research/laya_judge.py` (new)

Mirrors `research/semantic.py`'s shape exactly:

- `laya_available() -> bool` — `try: import laya_mlx` / `except ImportError: return False`.
  Never imported at module load time anywhere else in the package (matches `semantic.py`'s
  "gated, not default" comment in `quality.py`/`ablation.py`/`evolve.py`).
- `class LayaJudgeError(RuntimeError)` — raised when `use_laya=True` is requested but
  `laya_mlx` isn't installed, or the model call itself fails. Never a silent no-op fallback to
  the old (wrong) default — same principle `SemanticExpansionError` already establishes: a
  caller who asked for a real judgment and got none must be told, not quietly downgraded.
- `judge_redundancy(source: SourceFeatures, remaining: list[SourceFeatures], question: str) -> RedundancyJudgment` —
  loads and caches the `convaiinnovations/laya` agent (module-level singleton, loaded once per
  process — the ~36s checkpoint fetch/load is a one-time cost, not per-call), builds a `state`
  dict (`question`, the source's title/domain/content snippet, a short digest of what the
  *remaining* sources already cover), asks one `choice` question with criteria keys
  `redundant` / `load_bearing` (Laya's own naming convention — underscored typed-question
  keys). **`judge_redundancy` maps that key to this repo's existing hyphenated verdict
  constant (`"load-bearing"`, not `"load_bearing"`) before returning** — never passes Laya's
  criteria key through as the verdict string verbatim. This matters:
  `AblationReport.cut_candidates` / `.keep_locked` (ablation.py:87,91) filter on
  `v.verdict == "redundant"` / `v.verdict == "load-bearing"` exactly; a passed-through
  underscore would silently produce Laya-judged sources invisible to both. Returns a small dataclass:
  `verdict: str` (one of the existing `"redundant"` / `"load-bearing"` constants),
  `confidence: float`, `probabilities: dict[str, float]`.

### `research/ablation.py` (modified)

- `ablate_sources(..., use_laya: bool = False)` — new parameter, default `False`.
- In the classification step: when `use_laya` is true and the deterministic path lands in the
  ambiguous zone (`v_delta <= coverage_eps` and no `sole` terms and `c_delta <= composite_eps`
  — i.e. exactly the zone that currently defaults to `redundant`), call
  `laya_judge.judge_redundancy(...)` instead of returning `redundant` unconditionally. The
  Laya verdict becomes this source's `verdict`; `SourceValue` gains an optional
  `judge_confidence: float | None` field, populated only when Laya was consulted, so callers
  can see which verdicts came from the deterministic threshold vs. the tie-breaker.
  **`SourceValue.as_dict()` (hand-maintained, not `dataclasses.asdict()`) must also serialize
  `judge_confidence` — easy to add the field and forget the dict literal, at which point every
  MCP caller silently never sees it.**
- `use_laya=True` with `laya_mlx` not installed raises `LayaJudgeError` immediately (checked
  once at the top of `ablate_sources`, not per-source) rather than failing N times mid-loop.
- **Realistic cost, stated plainly**: the Problem section already establishes the ambiguous
  zone is the common case, not the exception — on the real 41-source notebook used to diagnose
  this bug, that's roughly 40 sources landing in it. At ~1s/call once Laya is warm, `use_laya=True`
  on that notebook adds **roughly 40 seconds** of sequential latency on top of the ~98s the
  deep-mode assess call already measured live this session (pre-Laya). This is the expected
  cost profile of turning this on, not a rare edge case — callers should treat `use_laya=True`
  as noticeably slower on any real notebook, not just "a few extra tie-break calls." If that
  cost is unacceptable in practice, the follow-up is a cap (consult Laya for at most K ambiguous
  sources, ranked by `|composite_delta|`) — out of scope for this spec, flagged here so it isn't
  a surprise during implementation or first real use.
  **The "~36s checkpoint load is one-time per process, not per-call" framing assumes the MCP
  server is a long-lived process for the session** (the standard MCP pattern: a client spawns
  `nlm-research-mcp` once and sends every tool call over that same stdio connection — this is
  how a real client like Claude Code uses it). It is **not** how this spec's own live testing
  worked: every test script this session spun up a fresh `nlm-research-mcp` subprocess per
  Python invocation, which would pay the 36s load on every single call. Implementation must
  confirm the real deployment shape before quoting "40s added" as the cost — under a short-lived-
  process pattern the real number is closer to 36s + 40s ≈ 76s, not 40s.

### `_tools.py` / MCP schema (modified)

- **Confirmed live**: `ToolLogic.assess_notebook` (`_tools.py:116`) calls
  `quick_assess(sources, question, weights=w, mode=mode)` (`_tools.py:133`), and `quick_assess`
  (`ablation.py:270`) is what actually calls `ablate_sources(sources, question, weights=w)`
  (`ablation.py:305`) — neither forwards a `use_laya` argument today. Adding the MCP schema
  flag alone (as originally drafted) has nowhere to land: `quick_assess` needs a `use_laya:
  bool = False` parameter that it threads through to its own `ablate_sources` call, and
  `assess_notebook` needs to accept and forward it, before the schema flag means anything.
- `research_assess`'s `mode="deep"` path gains the `use_laya` pass-through (schema:
  `{"use_laya": {"type": "boolean"}}`, default omitted = `False`), flowing
  schema → `assess_notebook` → `quick_assess` → `ablate_sources`. No change to `mode="fast"`
  (that path doesn't call `ablate_sources` at all).

### `pyproject.toml` (modified)

- New optional-dependency group: `laya = ["laya-mlx"]` — mirrors the existing `mcp = ["mcp<2"]`
  pattern. `pip install .` (no extras) is completely unaffected; `pip install '.[laya]'` pulls
  it in explicitly.

## Data flow

```
ablate_sources(sources, question, use_laya=True)
  for each source s:
    compute c_delta, v_delta, sole   (unchanged, always runs)
    if v_delta > eps or sole:
        verdict = load-bearing        (unchanged — clear signal, Laya never consulted)
    elif c_delta > composite_eps:
        verdict = marginal            (unchanged — clear signal, Laya never consulted)
    else:                             # <- the ambiguous zone gap #8 exposed
        if use_laya:
            judgment = laya_judge.judge_redundancy(s, rest, question)
            verdict = judgment.verdict
            judge_confidence = judgment.confidence
        else:
            verdict = redundant       (today's behavior, unchanged when use_laya=False)
```

## Error handling

- `use_laya=True`, `laya-mlx` not installed → `LayaJudgeError` at the top of `ablate_sources`,
  before any work starts. Message names the exact install command (`pip install
  'notelab[laya]'`).
- `use_laya=True`, checkpoint download/load fails (network, HF Hub rate limit, etc.) →
  `LayaJudgeError` wrapping the underlying exception, raised on first use (lazy singleton
  load), not at import time.
- `use_laya=False` (the default) → zero behavior change from today, zero new dependency ever
  imported. Existing callers, existing tests, existing offline install: unaffected.

**Supply-chain risk is a different class than `semantic.py`'s, not "the same gated pattern."**
`semantic.py` calls a hosted API — data in, data out, no code execution, nothing to trust
beyond the HTTPS response. `laya_judge.py` downloads and **executes** a third-party model
checkpoint from the Hugging Face Hub. `laya.load('convaiinnovations/laya')` must pin an exact
revision (commit hash or tagged version, not a floating `main`/`latest`), so a checkpoint
update upstream can't silently change this tie-breaker's behavior underneath a pinned notelab
release. This is a real difference from the Mistral precedent, not a detail — "opt-in" and
"gated" describe *whether* it runs, not *what* it's trusted to do once it does.

## Testing

- **Offline (default suite, no laya-mlx needed)**: a fake/stub judge (matching the existing
  `FakeAdapter`/`FakeRemover` pattern in `tests/`) verifies the wiring — `use_laya=True` with
  the zone-selection logic calls the injected judge only for ambiguous sources, never for
  clear ones; `use_laya=False` is provably unchanged (same output as before this change, on
  the existing N=3/N=6 synthetic fixtures already used to diagnose gap #8).
- **`use_laya=True` without `laya-mlx` installed**: offline test asserting `LayaJudgeError`,
  not an import crash or silent fallback.
- **Live, automated, gated test — asserting correctness, not just difference.** Matching the
  *actual* precedent this spec claims to follow: `research/semantic.py`'s Mistral backend has a
  real test in the suite today (`tests/test_semantic_expansions.py:134`, currently shown as
  `SKIPPED ... needs a real MISTRAL_API_KEY` — skipped, not absent) that asserts semantic
  *beats* lexical matching on a known fixture — an actual accuracy claim, not just "returns
  something." "Gets a real Laya verdict instead of the old unconditional `redundant`" is
  satisfied even if that verdict is wrong, as long as it differs — that's not evidence Laya
  helps. The test needs to reuse the **same synthetic 10x-strength fixture already built to
  diagnose gap #8** (the "strong"/"weak" source pairs at N=3/4/6 that expose the eps-scaling
  bug): assert that with `use_laya=True`, the genuinely strong source is judged `load-bearing`
  (not `redundant`) at N≥3 — the exact case the deterministic threshold gets wrong today. That's
  a correctness claim on known ground truth, matching the bar the Mistral precedent actually
  sets, gated on `laya_judge.laya_available()` (skip with a clear reason when `laya-mlx` isn't
  installed, run for real otherwise — no API key needed, just the local package).
  A manual smoke script (same pattern as `scripts/repro_results_2026-09-23.py`) against the
  real 41-source notebook is still worth having as a follow-up beyond CI, but it does not
  substitute for an automated test living in the suite the way the Mistral precedent has one.
