# Laya Ablation Tie-Breaker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix FIELD-NOTES.md gap #8 (ablation's `composite_eps` threshold only works at N=2 sources) by adding an opt-in Laya-based tie-breaker for the specific ambiguous zone the bug exposed.

**Architecture:** New `research/laya_judge.py` module (mirrors `research/semantic.py`'s gated, opt-in shape). `ablate_sources()` gains a `use_laya: bool = False` parameter; when true, sources landing in the ambiguous zone (small `composite_delta`, no coverage loss — exactly where the bug defaults to `redundant` unconditionally) get a real typed decision from Laya instead. Clear-signal sources never touch it. Threaded through `quick_assess()` → `_tools.py::assess_notebook()` → the MCP schema.

**Tech Stack:** Python stdlib (default path unaffected) + `laya-mlx` (new optional dependency, `pip install '.[laya]'` only).

Full design: `docs/superpowers/specs/2026-09-24-laya-ablation-judge-design.md` (reviewed by sc:spec-panel + GitHub Copilot, all findings applied).

---

### Task 1: `research/laya_judge.py` — new module

**Files:**
- Create: `src/notelab/research/laya_judge.py`
- Test: `tests/test_laya_judge.py`

- [ ] **Step 1: Write the failing offline test (mocks the agent, no laya-mlx needed)**

```python
"""laya_judge — the Laya-based ablation tie-breaker (research/laya_judge.py).

Offline tests mock the loaded agent's .predict() call (no laya-mlx import needed,
runs in CI). A separate live test only runs when laya_available() is True (mirrors
test_semantic_expansions.py's MISTRAL_API_KEY gate) and makes a real model call.
"""
from unittest.mock import patch

import pytest

from notelab.research.laya_judge import LayaJudgeError, judge_redundancy, laya_available
from notelab.research.quality import SourceFeatures


def _s(i, dom, text):
    return SourceFeatures(id=i, url=f"https://{dom}/{i}", content=text)


def test_laya_available_false_when_import_fails():
    with patch.dict("sys.modules", {"laya_mlx": None}):
        assert laya_available() is False


def test_judge_redundancy_maps_load_bearing_key_to_hyphenated_verdict():
    """The critical fix from spec-panel review: Laya's criteria key is
    'load_bearing' (underscore, Laya's own convention) but this repo's verdict
    string is 'load-bearing' (hyphen) -- ablate_sources' cut_candidates/keep_locked
    filter on the exact hyphenated string. A passed-through underscore would
    silently make Laya-judged sources invisible to both."""
    fake_agent = type("FakeAgent", (), {})()
    fake_agent.predict = lambda state, questions: {
        "answers": {
            "decision": {
                "type": "choice", "confidence": 0.9, "choice": "load_bearing",
                "probabilities": {"load_bearing": 0.9, "redundant": 0.1},
            }
        }
    }
    with patch("notelab.research.laya_judge._get_agent", return_value=fake_agent):
        judgment = judge_redundancy(
            _s("A", "arxiv.org", "real content"), [], "some question")
    assert judgment.verdict == "load-bearing"  # hyphenated, not "load_bearing"
    assert judgment.confidence == 0.9
    assert judgment.probabilities == {"load_bearing": 0.9, "redundant": 0.1}


def test_judge_redundancy_maps_redundant_key_unchanged():
    fake_agent = type("FakeAgent", (), {})()
    fake_agent.predict = lambda state, questions: {
        "answers": {
            "decision": {
                "type": "choice", "confidence": 0.7, "choice": "redundant",
                "probabilities": {"load_bearing": 0.3, "redundant": 0.7},
            }
        }
    }
    with patch("notelab.research.laya_judge._get_agent", return_value=fake_agent):
        judgment = judge_redundancy(
            _s("B", "example.com", "content"), [], "question")
    assert judgment.verdict == "redundant"


def test_judge_redundancy_raises_when_laya_not_available():
    with patch("notelab.research.laya_judge.laya_available", return_value=False):
        with pytest.raises(LayaJudgeError, match="laya-mlx"):
            judge_redundancy(_s("A", "x.com", "c"), [], "q")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_laya_judge.py -v`
Expected: `ModuleNotFoundError: No module named 'notelab.research.laya_judge'` (or `ImportError` — the module doesn't exist yet)

- [ ] **Step 3: Write the implementation**

```python
"""
laya_judge — Laya as an ablation tie-breaker (fixes FIELD-NOTES.md gap #8).

Gated, not default: `ablation.py` only imports this module's names, never
`laya_mlx` itself at module load time. A caller opts in explicitly via
`ablate_sources(..., use_laya=True)` — the stdlib-only, no-network default path
(`use_laya=False`, the default) is completely unaffected by this module existing,
same guarantee `research/semantic.py` makes for its Mistral backend.

Unlike semantic.py's hosted API call, this downloads and EXECUTES a third-party
model checkpoint from the Hugging Face Hub -- a different risk class, so the
revision is pinned to an exact commit, not a floating tag.

Laya (Convai Innovations, https://huggingface.co/convaiinnovations/laya) is a
421M-parameter, non-autoregressive decision model: given a `state` dict and typed
`questions` (choice/score/noul), it returns calibrated probabilities in a single
forward pass -- no text generation. Verified live: correctly picked the more
relevant of two real sources (80.6% probability) on a real research question.
"""
from __future__ import annotations

from dataclasses import dataclass

# Pinned to the exact commit fetched and verified live this session -- a checkpoint
# update upstream must not silently change this tie-breaker's behavior underneath a
# pinned notelab release.
_MODEL_ID = "convaiinnovations/laya"
_PINNED_REVISION = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"

# Laya's own typed-question key convention (underscored) -> this repo's existing
# verdict string convention (hyphenated). ablate_sources' AblationReport.cut_candidates
# / .keep_locked filter on the exact hyphenated string ("load-bearing"); passing
# Laya's raw key through would silently break both.
_VERDICT_MAP = {"redundant": "redundant", "load_bearing": "load-bearing"}

_agent = None  # module-level singleton: the ~36s checkpoint load is one-time per
                # process (assumes a long-lived MCP server process -- see spec's
                # "Realistic cost" note if that assumption doesn't hold), not per-call.


class LayaJudgeError(RuntimeError):
    """laya-mlx isn't installed, or the model call itself failed. Never a silent
    no-op fallback to the old (wrong) default -- same principle SemanticExpansionError
    already establishes: a caller who asked for a real judgment and got none must be
    told, not quietly downgraded."""


@dataclass
class RedundancyJudgment:
    verdict: str  # "redundant" | "load-bearing" -- one of the existing constants
    confidence: float
    probabilities: dict[str, float]


def laya_available() -> bool:
    try:
        import laya_mlx  # noqa: F401
    except ImportError:
        return False
    return True


def _get_agent():
    global _agent
    if _agent is None:
        if not laya_available():
            raise LayaJudgeError(
                "laya-mlx is not installed — pip install 'notelab[laya]'")
        import laya_mlx
        try:
            _agent = laya_mlx.load(_MODEL_ID, revision=_PINNED_REVISION)
        except Exception as e:
            raise LayaJudgeError(f"failed to load Laya checkpoint: {e}") from e
    return _agent


def judge_redundancy(source, remaining: list, question: str) -> RedundancyJudgment:
    """Ask Laya: does removing `source` (leaving `remaining`) actually hurt
    coverage of `question`? Only called by ablate_sources for the ambiguous zone
    the fixed-epsilon threshold gets wrong (ties.eps.02 vs a per-source mean that
    shrinks as ~1/N — see FIELD-NOTES.md gap #8)."""
    if not laya_available():
        raise LayaJudgeError(
            "use_laya=True but laya-mlx is not installed — pip install 'notelab[laya]'")
    agent = _get_agent()

    digest = "; ".join((s.title or s.domain or s.id) for s in remaining[:10]) or "(none)"
    state = {
        "question": question,
        "source_title": source.title or "(untitled)",
        "source_domain": source.domain or "(no domain)",
        "source_snippet": (source.content or "")[:500],
        "remaining_sources_digest": digest,
    }
    questions = {
        "decision": {
            "type": "choice",
            "instructions": (
                "Given question, does removing source_title (content: "
                "source_snippet) from the source set actually hurt coverage of "
                "the question, given what remaining_sources_digest already covers?"
            ),
            "criteria": {
                "redundant": "the remaining sources already cover what this source contributes",
                "load_bearing": "removing this source would leave a real gap in answering the question",
            },
        },
    }
    try:
        result = agent.predict(state, questions)
    except Exception as e:
        raise LayaJudgeError(f"Laya prediction failed: {e}") from e

    answer = result["answers"]["decision"]
    key = answer["choice"]
    return RedundancyJudgment(
        verdict=_VERDICT_MAP[key],
        confidence=answer["confidence"],
        probabilities=answer["probabilities"],
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_laya_judge.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/notelab/research/laya_judge.py tests/test_laya_judge.py
git commit -m "feat: add laya_judge module (FIELD-NOTES gap #8 tie-breaker)"
```

---

### Task 2: Wire `use_laya` into `ablate_sources`

**Files:**
- Modify: `src/notelab/research/ablation.py:1-22` (module docstring), `:56-69` (SourceValue), `:95-137` (ablate_sources)
- Test: `tests/test_ablation.py` (add to existing file)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ablation.py`:

```python
from unittest.mock import patch

from notelab.research import laya_judge
from notelab.research.laya_judge import LayaJudgeError, RedundancyJudgment


def _ambiguous_fixture():
    # 3 near-identical strong sources on the SAME topic -> all land in the
    # ambiguous zone at N=3 (mirrors the gap #8 diagnosis: composite_delta too
    # small to clear eps regardless of real quality, no single sole-cover term).
    return [
        _s("A", "arxiv.org", Q, n=50),
        _s("B", "nature.com", Q, n=50),
        _s("C", "some-lab.org", Q, n=50),
    ]


def test_use_laya_false_is_unchanged_from_before():
    rep = ablate_sources(_ambiguous_fixture(), Q, use_laya=False)
    assert all(v.verdict == "redundant" for v in rep.per_source)  # today's bug, unchanged
    assert all(v.judge_confidence is None for v in rep.per_source)


def test_use_laya_true_calls_judge_only_for_ambiguous_sources():
    fixture = [
        _s("A", "arxiv.org", "quantum error correction surface code threshold"),
        _s("B", "nature.com", "quantum error correction surface code threshold"),
        _s("C", "some-lab-blog.org", "decoder algorithm belief propagation"),  # sole cover
    ]
    with patch.object(laya_judge, "judge_redundancy") as mock_judge:
        mock_judge.return_value = RedundancyJudgment(
            verdict="redundant", confidence=0.6, probabilities={"redundant": 0.6, "load_bearing": 0.4})
        rep = ablate_sources(fixture, Q, use_laya=True)
    # C has a sole_cover_term -> load-bearing via the CLEAR signal, never touches Laya
    called_ids = [call.args[0].id for call in mock_judge.call_args_list]
    assert "C" not in called_ids
    by = {v.id: v for v in rep.per_source}
    assert by["C"].verdict == "load-bearing"
    assert by["C"].judge_confidence is None  # clear-signal path, Laya never consulted


def test_use_laya_true_applies_judge_verdict_and_confidence():
    with patch.object(laya_judge, "judge_redundancy") as mock_judge:
        mock_judge.return_value = RedundancyJudgment(
            verdict="load-bearing", confidence=0.83, probabilities={"load_bearing": 0.83, "redundant": 0.17})
        rep = ablate_sources(_ambiguous_fixture(), Q, use_laya=True)
    assert mock_judge.call_count == 3  # all 3 are ambiguous
    assert all(v.verdict == "load-bearing" for v in rep.per_source)
    assert all(v.judge_confidence == 0.83 for v in rep.per_source)


def test_use_laya_true_without_laya_mlx_raises_immediately():
    with patch.object(laya_judge, "laya_available", return_value=False):
        with pytest.raises(LayaJudgeError):
            ablate_sources(_ambiguous_fixture(), Q, use_laya=True)


def test_source_value_as_dict_includes_judge_confidence_only_when_set():
    rep_off = ablate_sources(_ambiguous_fixture(), Q, use_laya=False)
    assert "judge_confidence" not in rep_off.per_source[0].as_dict()

    with patch.object(laya_judge, "judge_redundancy") as mock_judge:
        mock_judge.return_value = RedundancyJudgment(
            verdict="redundant", confidence=0.55, probabilities={})
        rep_on = ablate_sources(_ambiguous_fixture(), Q, use_laya=True)
    d = rep_on.per_source[0].as_dict()
    assert d["judge_confidence"] == 0.55
```

Add `import pytest` at the top of `tests/test_ablation.py` if not already present.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ablation.py -v -k use_laya`
Expected: `TypeError: ablate_sources() got an unexpected keyword argument 'use_laya'`

- [ ] **Step 3: Update the module docstring**

In `src/notelab/research/ablation.py`, replace line 21:

```python
Deterministic + offline: reuses `quality.score_sources`. No network, no keys.
```

with:

```python
Deterministic and offline BY DEFAULT: reuses `quality.score_sources`. No network,
no keys, unless the caller opts into `use_laya=True` (research/laya_judge.py) — a
network-backed, non-deterministic tie-breaker for the specific ambiguous zone a
fixed epsilon threshold can't classify past a few sources (see FIELD-NOTES.md gap
#8). `evolve.py` consumes these verdicts for evict/keep decisions — anyone relying
on ablation being reproducible should know `use_laya=True` changes that.
```

- [ ] **Step 4: Add the `laya_judge` import and `judge_confidence` field**

In `src/notelab/research/ablation.py`, after the existing imports (line 30):

```python
from . import laya_judge
```

Replace the `SourceValue` class (lines 56-69):

```python
@dataclass
class SourceValue:
    id: str
    composite_delta: float   # how much composite DROPS if this source is removed
    coverage_delta: float    # how much question-coverage DROPS if removed
    sole_cover_terms: list[str]  # question terms ONLY this source covers
    verdict: str             # "load-bearing" | "redundant" | "marginal"
    judge_confidence: float | None = None  # set only when use_laya consulted Laya
                                            # for this source's verdict

    def as_dict(self) -> dict:
        d = {"id": self.id,
             "composite_delta": round(self.composite_delta, 4),
             "coverage_delta": round(self.coverage_delta, 4),
             "sole_cover_terms": self.sole_cover_terms,
             "verdict": self.verdict}
        if self.judge_confidence is not None:
            d["judge_confidence"] = round(self.judge_confidence, 4)
        return d
```

- [ ] **Step 5: Wire `use_laya` into `ablate_sources`**

Replace the `ablate_sources` function (lines 95-137):

```python
def ablate_sources(
    sources: list[SourceFeatures],
    question: str,
    weights: QualityWeights | None = None,
    composite_eps: float = 0.02,   # composite drop below this = negligible
    coverage_eps: float = 1e-9,    # any coverage loss at all = load-bearing
    use_laya: bool = False,
) -> AblationReport:
    """Leave-one-out ablation. For each source, recompute composite + coverage over
    the set WITHOUT it, and classify:
      - load-bearing: removing it loses question-coverage (sole cover for a term),
        OR drops composite by more than composite_eps;
      - redundant:    removing it changes neither axis beyond eps (or, with
        use_laya=True, Laya judges it redundant for this specific ambiguous case);
      - marginal:     small composite drop but no coverage loss.

    use_laya=True: composite_delta shrinks as ~1/N by construction (it's a
    leave-one-out shift in a MEAN over N sources), so the fixed composite_eps only
    reliably classifies at N=2 (verified: FIELD-NOTES.md gap #8). For sources that
    land in that ambiguous zone, ask Laya (research/laya_judge.py) instead of
    defaulting to "redundant" unconditionally. Sources with a clear signal (real
    coverage loss, or a delta that clears the threshold on its own) never touch
    Laya. Raises LayaJudgeError immediately if use_laya=True and laya-mlx isn't
    installed, before any work starts.
    """
    if use_laya and not laya_judge.laya_available():
        raise laya_judge.LayaJudgeError(
            "use_laya=True but laya-mlx is not installed — pip install 'notelab[laya]'")

    w = weights or QualityWeights()
    qterms = _content_terms(question)
    exp = w.term_expansions
    full = score_sources(sources, question=question, weights=w)
    full_cov = _coverage(sources, qterms, exp)

    out: list[SourceValue] = []
    for i, s in enumerate(sources):
        rest = sources[:i] + sources[i + 1:]
        rep = score_sources(rest, question=question, weights=w)
        cov = _coverage(rest, qterms, exp)
        c_delta = full.quality_score - rep.quality_score
        v_delta = full_cov - cov
        # terms only this source covers (expansion-aware):
        others_cover: set[str] = set()
        for o in rest:
            others_cover |= _covered_terms(o, qterms, exp)
        mine = _covered_terms(s, qterms, exp)
        sole = sorted(mine - others_cover)

        judge_confidence = None
        if v_delta > coverage_eps or sole:
            verdict = "load-bearing"
        elif c_delta <= composite_eps:
            if use_laya:
                judgment = laya_judge.judge_redundancy(s, rest, question)
                verdict = judgment.verdict
                judge_confidence = judgment.confidence
            else:
                verdict = "redundant"
        else:
            verdict = "marginal"
        out.append(SourceValue(s.id, c_delta, v_delta, sole, verdict, judge_confidence))

    return AblationReport(question, len(sources), full.quality_score, full_cov, out)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ablation.py -v`
Expected: all tests pass, including the pre-existing ones (unchanged behavior at `use_laya=False`)

- [ ] **Step 7: Run the full offline suite to check nothing else broke**

Run: `.venv/bin/pytest -q`
Expected: all pass (previous count + new tests), same 1 skip (MISTRAL_API_KEY)

- [ ] **Step 8: Commit**

```bash
git add src/notelab/research/ablation.py tests/test_ablation.py
git commit -m "feat: wire use_laya into ablate_sources as an opt-in tie-breaker"
```

---

### Task 3: Thread `use_laya` through `quick_assess` → `assess_notebook` → MCP schema

**Files:**
- Modify: `src/notelab/research/ablation.py:270-312` (`quick_assess`)
- Modify: `src/notelab/_tools.py:116-133` (`assess_notebook`), `:226-228` (schema), `:256-258` (description)
- Test: `tests/test_quick_assess_wiring.py` (check existing file for the pattern; add to it) or `tests/test_ablation.py`

**Confirmed live** (spec-panel + Copilot review both caught the original draft missing this): `assess_notebook` calls `quick_assess(sources, question, weights=w, mode=mode)`, and `quick_assess` is what actually calls `ablate_sources(sources, question, weights=w)` — neither forwarded `use_laya` before this task.

- [ ] **Step 1: Write the failing test**

```python
def test_quick_assess_deep_mode_forwards_use_laya():
    from unittest.mock import patch
    from notelab.research import laya_judge
    from notelab.research.laya_judge import RedundancyJudgment
    from notelab.research.ablation import quick_assess

    fixture = _ambiguous_fixture()  # reuse from test_ablation.py, or redefine locally
    with patch.object(laya_judge, "judge_redundancy") as mock_judge:
        mock_judge.return_value = RedundancyJudgment(
            verdict="load-bearing", confidence=0.7, probabilities={})
        result = quick_assess(fixture, Q, mode="deep", use_laya=True)
    assert mock_judge.call_count == 3
    assert all(s["verdict"] == "load-bearing" for s in result["ablation"]["per_source"])


def test_quick_assess_fast_mode_ignores_use_laya():
    """mode="fast" never calls ablate_sources at all -- use_laya must be a silent
    no-op there, not an error, since the fast lane's whole point is skipping the
    O(n^2) pipeline entirely."""
    from notelab.research.ablation import quick_assess
    result = quick_assess(_ambiguous_fixture(), Q, mode="fast", use_laya=True)
    assert result["mode"] == "fast"  # didn't raise, didn't touch ablation
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_ablation.py -v -k quick_assess_deep_mode_forwards`
Expected: `TypeError: quick_assess() got an unexpected keyword argument 'use_laya'`

- [ ] **Step 3: Update `quick_assess`**

In `src/notelab/research/ablation.py`, replace the `quick_assess` signature and its deep-mode branch (lines 270-312):

```python
def quick_assess(
    sources: list[SourceFeatures],
    question: str,
    weights: QualityWeights | None = None,
    mode: str = "fast",
    use_laya: bool = False,
) -> dict:
    """Lightweight lane for easy tasks / quick improvements.

    mode="fast": a single O(n) pass — composite, coverage, and a coverage-gap list —
    with NO O(n^2) leave-one-out. Cheap enough to run on every research return and
    mirror NotebookLM's own fast research mode. Returns a compact verdict:
        - "good"        composite high AND full coverage — nothing to do;
        - "thin"        low composite or missing coverage — deepen (more sources);
        - "bloated"     full coverage but many sources — candidate for greedy_prune.
    mode="deep": defers to the full ablation + greedy pipeline (callers escalate here
    only when fast flags something), so easy tasks never pay the quadratic cost.
    `use_laya` only applies here — ignored (not an error) in fast mode, which
    never calls ablate_sources at all.

    `mode` is case-insensitive but otherwise strict: anything besides "fast"/"deep"
    raises rather than silently falling through to the cheap lane (sparring review
    caught this — a typo like mode="Deep" must not silently downgrade to fast).
    """
    mode = mode.lower()
    if mode not in ("fast", "deep"):
        raise ValueError(f"quick_assess: mode must be 'fast' or 'deep', got {mode!r}")
    w = weights or QualityWeights()
    qterms = _content_terms(question)
    exp = w.term_expansions
    rep = score_sources(sources, question=question, weights=w)
    cov = _coverage(sources, qterms, exp)
    covered = set()
    for s in sources:
        covered |= _covered_terms(s, qterms, exp)
    gaps = sorted(qterms - covered)

    if mode == "deep":
        abl = ablate_sources(sources, question, weights=w, use_laya=use_laya)
        pairs = find_redundant_pairs(sources, question, weights=w)
        prune = greedy_prune(sources, question, weights=w)
        return {"mode": "deep", "composite": round(rep.quality_score, 4),
                "coverage": round(cov, 4), "coverage_gaps": gaps,
                "ablation": abl.as_dict(),
                "redundant_pairs": [p.as_dict() for p in pairs],
                "greedy_prune": prune.as_dict()}

    # fast lane
    if not qterms:
        # no question terms to check coverage against — a bare source/composite
        # score can't honestly claim "good" or "bloated" (sparring review: an empty
        # question was silently reporting "good" off composite/count alone).
        verdict = "unassessable"
    elif gaps or rep.quality_score < 0.45:
        verdict = "thin"
    elif len(sources) > max(3, 2 * max(1, len(qterms))):
        verdict = "bloated"
    else:
        verdict = "good"
    return {"mode": "fast", "verdict": verdict,
            "composite": round(rep.quality_score, 4),
            "coverage": round(cov, 4), "coverage_gaps": gaps,
            "n_sources": len(sources),
            "escalate_to_deep": verdict != "good"}
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_ablation.py -v`
Expected: all pass

- [ ] **Step 5: Wire `assess_notebook` and the MCP schema**

In `src/notelab/_tools.py`, replace `assess_notebook` (lines 116-133):

```python
    def assess_notebook(self, notebook_id: str, question: str = "", mode: str = "fast",
                        weights: dict | None = None, use_laya: bool = False) -> str:
        """Lightweight (fast) or full (deep) value assessment of an existing
        notebook's CURRENT source set — same engine research_run_experiment wires
        into every arm automatically, exposed here for on-demand / deep calls.

        Content backfill is capped at 15 sources for the cheap fast lane (matches
        score_notebook/evolve_notebook), but deep mode backfills ALL sources: it
        already commits to the full O(n^2) ablation/pairwise/greedy-prune pipeline,
        so silently scoring sources 16+ as contentless would corrupt exactly the
        thorough result the caller asked for (sparring review caught this).

        use_laya: opt-in tie-breaker for ablation's ambiguous zone (mode="deep"
        only, ignored in mode="fast" — see quick_assess). Requires laya-mlx
        installed (pip install 'notelab[laya]'); raises LayaJudgeError if not."""
        w = _weights_from(weights)
        sources = self.adapter.list_sources(notebook_id)
        to_fill = sources if mode.lower() == "deep" else sources[:15]
        for s in to_fill:
            if not s.content:
                s.content = self.adapter.source_content(s.id)
        return json.dumps(
            quick_assess(sources, question, weights=w, mode=mode, use_laya=use_laya),
            indent=2)
```

Update the schema entry (lines 226-228):

```python
    "research_assess": {"type": "object", "required": ["notebook_id"],
        "properties": {"notebook_id": {"type": "string"}, "question": {"type": "string"},
                       "mode": {"type": "string"}, "weights": {"type": "object"},
                       "use_laya": {"type": "boolean"}}},
```

Update the description (lines 256-258):

```python
    "research_assess": "Value-assess a notebook's current sources: mode='fast' (cheap, "
        "good/thin/bloated + coverage gaps) or 'deep' (full ablation + redundant-pairs + "
        "greedy-prune). use_laya=true (deep mode only) opts into a Laya tie-breaker for "
        "ambiguous redundant/load-bearing calls — requires laya-mlx installed.",
```

- [ ] **Step 6: Run the full offline suite**

Run: `.venv/bin/pytest -q`
Expected: all pass, same 1 skip

- [ ] **Step 7: Commit**

```bash
git add src/notelab/research/ablation.py src/notelab/_tools.py tests/test_ablation.py
git commit -m "feat: thread use_laya through quick_assess -> assess_notebook -> MCP schema"
```

---

### Task 4: `pyproject.toml` — optional dependency group

**Files:**
- Modify: `pyproject.toml:17-23`

- [ ] **Step 1: Add the `laya` extra**

Replace lines 17-23:

```toml
[project.optional-dependencies]
# Optional accelerator: the official MCP SDK. Pinned <2 because 2.x renamed FastMCP
# to MCPServer and changed APIs. NOT required — the repo ships its own stdlib-only
# stdio MCP server (see notelab.mcp_stdio), so `pip install .` runs with no
# third-party deps at all.
mcp = ["mcp<2"]
dev = ["pytest>=7.0"]
# Optional: the Laya ablation tie-breaker (research/laya_judge.py, FIELD-NOTES.md
# gap #8). NOT required — use_laya defaults to False, the stdlib-only default path
# is completely unaffected by this extra existing.
laya = ["laya-mlx"]
```

- [ ] **Step 2: Verify the default install is still dependency-free**

Run: `uv venv --python 3.12 /tmp/notelab-verify && uv pip install --python /tmp/notelab-verify/bin/python -e .`
Expected: installs `notelab` only, zero third-party packages (matches the existing guarantee)

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add laya optional-dependency group"
```

---

### Task 5: Live, automated, gated correctness test

**Files:**
- Create: `tests/test_laya_ablation_live.py`

This is the test spec-panel + Copilot review both flagged as missing: the offline tests (Task 2) prove the *wiring* is correct (right sources get consulted, verdict gets mapped correctly) using a mocked judge — they don't prove Laya's *actual judgment* is correct. This test uses the real model, gated exactly like `test_semantic_expansions.py`'s MISTRAL_API_KEY test, and reuses gap #8's own diagnostic fixture (a real 10x content-strength gap) to assert the CORRECT verdict, not just a different one.

- [ ] **Step 1: Write the test**

```python
"""Live correctness test for the Laya ablation tie-breaker (research/laya_judge.py).

Gated on laya_available() (skip, don't fail, when laya-mlx isn't installed) --
mirrors tests/test_semantic_expansions.py's MISTRAL_API_KEY gate. Reuses the same
synthetic 10x content-strength fixture that diagnosed FIELD-NOTES.md gap #8 (the
fixed composite_eps misclassifying a genuinely strong source as "redundant" at
N=3+) to assert Laya gets it RIGHT where the deterministic threshold gets it wrong
-- not just "returns a verdict, any verdict" (the bar Copilot's review flagged the
original draft as not meeting).
"""
import pytest

from notelab.research.ablation import ablate_sources
from notelab.research.laya_judge import laya_available
from notelab.research.quality import SourceFeatures


def _s(i, dom, text, n):
    return SourceFeatures(id=i, url=f"https://{dom}/{i}", content=(text + " ") * n)


Q = "PSD2 aggregator BaFin regulation for German fintech"


def _strong_weak_fixture():
    # the exact 10x content-strength gap used to diagnose gap #8 at N=3: without
    # use_laya, the deterministic threshold misclassifies the strong source as
    # "redundant" here (verified: deltas -0.019 to 0.009, all under composite_eps).
    strong_text = ("PSD2 open banking aggregator FinAPI Tink GoCardless BaFin "
                   "Anlageberatung regulation German fintech consent flow pricing")
    weak_text = "general banking history overview"
    return [
        _s("strong", "finapi.io", strong_text, n=50),
        _s("weak_a", "example.com", weak_text, n=50),
        _s("weak_b", "other.com", weak_text, n=50),
    ]


@pytest.mark.skipif(not laya_available(),
                    reason="live Laya test needs laya-mlx installed (pip install 'notelab[laya]')")
def test_laya_correctly_judges_strong_source_load_bearing_where_threshold_fails():
    # confirm the deterministic threshold alone still gets this wrong (the bug
    # gap #8 documents) before checking Laya fixes it
    rep_off = ablate_sources(_strong_weak_fixture(), Q, use_laya=False)
    strong_off = {v.id: v for v in rep_off.per_source}["strong"]
    assert strong_off.verdict == "redundant"  # the documented bug, still present

    rep_on = ablate_sources(_strong_weak_fixture(), Q, use_laya=True)
    strong_on = {v.id: v for v in rep_on.per_source}["strong"]
    assert strong_on.verdict == "load-bearing"
    assert strong_on.judge_confidence is not None
```

- [ ] **Step 2: Run it (laya-mlx is installed in this dev environment)**

Run: `.venv/bin/pytest tests/test_laya_ablation_live.py -v`
Expected: PASS (first run pays the ~36s checkpoint load)

If it fails on the real judgment (Laya says "redundant" instead of "load-bearing"), do not weaken the assertion — that's a real signal the prompt/criteria text in `laya_judge.py`'s `judge_redundancy` needs adjusting, or that this particular fixture isn't a clean enough signal. Iterate on the fixture or the criteria wording, not the test.

- [ ] **Step 3: Run the full suite one more time to confirm nothing regressed**

Run: `.venv/bin/pytest -q`
Expected: all pass (previous count + new tests), 1 skip only if `MISTRAL_API_KEY` unset (the Laya test runs since laya-mlx IS installed here)

- [ ] **Step 4: Commit**

```bash
git add tests/test_laya_ablation_live.py
git commit -m "test: live correctness test for the Laya tie-breaker against gap #8's own fixture"
```

---

### Task 6: Manual live smoke test + FIELD-NOTES.md follow-up

**Files:**
- Modify: `docs/FIELD-NOTES.md` (gap #8 section)

- [ ] **Step 1: Re-run the real 41-source notebook assessment with `use_laya=True`**

Using the same pattern as the session's earlier live testing (a short Python script talking JSON-RPC to `nlm-research-mcp`), call `research_assess` with `mode="deep", use_laya=true` against notebook `00000000-0000-4000-8000-000000000000` (the real "practitioner" finance-research notebook used to diagnose gap #8), question `"PSD2 aggregator BaFin regulation for German fintech"`.

- [ ] **Step 2: Confirm the all-41-`redundant` result no longer holds**

Compare the `verdict` distribution against the original all-`redundant` result documented in FIELD-NOTES.md gap #8.

- [ ] **Step 3: Update FIELD-NOTES.md gap #8**

Mark it `FIXED` (matching the format of gaps #1 and #6), with the real verdict distribution from Step 2 as evidence, same style as the rest of the document.

- [ ] **Step 4: Commit**

```bash
git add docs/FIELD-NOTES.md
git commit -m "docs: mark FIELD-NOTES gap #8 fixed, record live use_laya=True verdict distribution"
```

---

## Self-review (spec coverage)

- Verdict-string mapping bug (spec-panel finding): Task 1, Step 1/3 — dedicated test + explicit `_VERDICT_MAP`. ✓
- `as_dict()` serialization gap (spec-panel finding): Task 2, Step 4/5 — conditional inclusion, tested. ✓
- Realistic latency cost (Nygard/Copilot finding): documented in spec and module docstrings; no code change needed (informational), Task 6's live run gives a real number for this specific notebook. ✓
- Determinism-guarantee conflict (Copilot finding): Task 2, Step 3 — docstring update. ✓
- `quick_assess`/`assess_notebook` wiring gap (Copilot finding, confirmed live): Task 3, all steps. ✓
- Correctness evidence, not just difference (Copilot finding): Task 5 — reuses gap #8's own fixture, asserts the correct verdict. ✓
- Supply-chain pinning (Copilot finding): Task 1, Step 3 — `_PINNED_REVISION`, a real commit hash fetched live via `HfApi().model_info(...)`. ✓
- Non-default behavior, bug not fixed unless opted in (Copilot finding #1): stated in spec's "Consequence of staying opt-in" section; Task 2's `test_use_laya_false_is_unchanged_from_before` proves it explicitly. ✓
- `pyproject.toml` optional-dependency group: Task 4. ✓
