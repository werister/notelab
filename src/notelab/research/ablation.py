"""
Leave-one-out ablation A/B — "does this source actually add value?"

The A/B harness in `ab.py` ranks whole strategies by composite score, and
`evolve.py` decides keep/cut on a source's STANDALONE score + domain-duplication.
Neither answers the marginal question: *if I remove this source, does the set get
worse at answering the question?* A high-authority source that two others already
cover adds ~0 marginal value; a modest source may be the SOLE cover for a sub-aspect.

This module measures each source's MARGINAL contribution by scoring the set with vs
without it, on two axes:
  * `composite_delta` — drop in the quality composite when the source is removed;
  * `coverage_delta`   — drop in question-term COVERAGE (fraction of the question's
                         terms still covered by the remaining set). This is what
                         catches "the only source covering sub-topic X".

A source is REDUNDANT (safe-to-cut) when removing it barely moves either axis; it is
LOad-BEARING (keep) when removing it drops coverage or composite meaningfully. This
gives `evolve.py` an evidence-based evict/keep signal instead of standalone score.

Deterministic and offline BY DEFAULT: reuses `quality.score_sources`. No network,
no keys, unless the caller opts into `use_laya=True` (research/laya_judge.py) — a
network-backed, non-deterministic tie-breaker for the specific ambiguous zone a
fixed epsilon threshold can't classify past a few sources (see FIELD-NOTES.md gap
#8). `evolve.py` consumes these verdicts for evict/keep decisions — anyone relying
on ablation being reproducible should know `use_laya=True` changes that.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import laya_judge
from .quality import (
    QualityWeights, SourceFeatures, score_sources, _content_terms,
)


def _covered_terms(s: SourceFeatures, question_terms: set[str],
                   expansions: dict[str, set[str]] | None = None) -> set[str]:
    """Question terms this source covers — directly OR via a configured expansion."""
    exp = expansions or {}
    terms = _content_terms(f"{s.title} {s.content}")
    out: set[str] = set()
    for qt in question_terms:
        if qt in terms or (exp.get(qt) and (exp[qt] & terms)):
            out.add(qt)
    return out


def _coverage(sources: list[SourceFeatures], question_terms: set[str],
              expansions: dict[str, set[str]] | None = None) -> float:
    """Fraction of the question's terms covered by AT LEAST ONE source in the set."""
    if not question_terms:
        return 0.0
    covered: set[str] = set()
    for s in sources:
        covered |= _covered_terms(s, question_terms, expansions)
    return len(covered) / len(question_terms)


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


@dataclass
class AblationReport:
    question: str
    n_sources: int
    full_composite: float
    full_coverage: float
    per_source: list[SourceValue]

    def as_dict(self) -> dict:
        return {"question": self.question, "n_sources": self.n_sources,
                "full_composite": round(self.full_composite, 4),
                "full_coverage": round(self.full_coverage, 4),
                "per_source": [v.as_dict() for v in self.per_source]}

    @property
    def cut_candidates(self) -> list[str]:
        return [v.id for v in self.per_source if v.verdict == "redundant"]

    @property
    def keep_locked(self) -> list[str]:
        return [v.id for v in self.per_source if v.verdict == "load-bearing"]


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


@dataclass
class RedundantPair:
    """Two sources that cover the SAME question terms — interchangeable. Keeping both
    is redundant; the lower-scoring one is the drop candidate. Single-source
    leave-one-out cannot see this (remove one, the twin still covers), so it needs an
    explicit pairwise pass."""
    keep_id: str
    drop_id: str
    shared_terms: list[str]

    def as_dict(self) -> dict:
        return {"keep_id": self.keep_id, "drop_id": self.drop_id,
                "shared_terms": self.shared_terms}


def find_redundant_pairs(
    sources: list[SourceFeatures],
    question: str,
    weights: QualityWeights | None = None,
    min_shared_terms: int = 1,
) -> list[RedundantPair]:
    """Pairwise redundancy over question-term coverage.

    Two sources are interchangeable when their covered-question-term SETS are equal
    and non-empty (they answer exactly the same part of the question). The one with
    the lower standalone composite is the drop candidate; the stronger is kept. Only
    pairs sharing >= min_shared_terms are considered. Deterministic, offline.
    """
    w = weights or QualityWeights()
    qterms = _content_terms(question)
    if not qterms:
        return []

    covers: dict[str, frozenset] = {}
    solo_score: dict[str, float] = {}
    for s in sources:
        c = frozenset(_content_terms(f"{s.title} {s.content}") & qterms)
        covers[s.id] = c
        solo_score[s.id] = score_sources([s], question=question, weights=w).quality_score

    pairs: list[RedundantPair] = []
    seen_dropped: set[str] = set()
    ids = [s.id for s in sources]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            if a in seen_dropped or b in seen_dropped:
                continue
            ca, cb = covers[a], covers[b]
            if ca and ca == cb and len(ca) >= min_shared_terms:
                # interchangeable: keep the stronger, drop the weaker
                if solo_score[a] >= solo_score[b]:
                    keep, drop = a, b
                else:
                    keep, drop = b, a
                pairs.append(RedundantPair(keep, drop, sorted(ca)))
                seen_dropped.add(drop)
    return pairs


@dataclass
class PruneStep:
    dropped_id: str
    reason: str
    coverage_after: float


@dataclass
class GreedyPruneResult:
    kept_ids: list[str]
    dropped: list[PruneStep]
    full_coverage: float
    kept_coverage: float

    def as_dict(self) -> dict:
        return {"kept_ids": self.kept_ids,
                "dropped": [{"id": d.dropped_id, "reason": d.reason,
                             "coverage_after": round(d.coverage_after, 4)} for d in self.dropped],
                "full_coverage": round(self.full_coverage, 4),
                "kept_coverage": round(self.kept_coverage, 4)}


def greedy_prune(
    sources: list[SourceFeatures],
    question: str,
    weights: QualityWeights | None = None,
    min_keep: int = 1,
    preserve_coverage: bool = True,
) -> GreedyPruneResult:
    """Greedy N-way redundancy reduction.

    Iteratively drop the LOWEST-scoring source whose removal does not reduce
    question-coverage (when preserve_coverage=True), until nothing more can go or
    `min_keep` sources remain. This catches redundancy that pairwise cannot: 3+
    sources covering the same terms collapse to the single strongest, because each
    removal is re-evaluated against the shrinking set. Deterministic, offline.
    """
    w = weights or QualityWeights()
    qterms = _content_terms(question)
    exp = w.term_expansions
    kept = list(sources)
    full_cov = _coverage(kept, qterms, exp)
    dropped: list[PruneStep] = []

    # weakest first as removal candidates
    def solo(s):
        return score_sources([s], question=question, weights=w).quality_score

    changed = True
    while changed and len(kept) > min_keep:
        changed = False
        for cand in sorted(kept, key=solo):  # try weakest first
            if len(kept) <= min_keep:
                break
            trial = [s for s in kept if s.id != cand.id]
            trial_cov = _coverage(trial, qterms, exp)
            if not preserve_coverage or trial_cov >= full_cov:
                dropped.append(PruneStep(
                    cand.id,
                    f"redundant — removal keeps coverage at {trial_cov:.3f}",
                    trial_cov))
                kept = trial
                changed = True
                break  # re-evaluate from the (smaller) set

    return GreedyPruneResult(
        kept_ids=[s.id for s in kept], dropped=dropped,
        full_coverage=full_cov, kept_coverage=_coverage(kept, qterms, exp))


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
