"""
Self-evolving source management.

Modeled on volcengine/OpenViking's memory consolidation — which is NOT naive
time-decay but a **create / merge / skip** pipeline plus **score-ranked, budgeted
recall**. We adapt that to a NotebookLM notebook's SOURCE set:

  - CREATE : a new candidate source that is novel and above the quality floor → keep.
  - MERGE  : a candidate that duplicates an existing source (same registrable domain
             + high title/content overlap) → drop the weaker of the pair.
  - SKIP   : a candidate below the quality floor and adding no diversity → don't add.
  - EVICT  : an EXISTING source whose decayed score has sat below the floor across
             >= `evict_after_strikes` observations → propose removal.

Decay is the surrogate for "no new content over time": each observation multiplies a
source's effective score by a decay factor unless the source was refreshed (its
content/score improved). A source that is never refreshed and never wins its keep-slot
fades out — exactly OpenViking's "low-score memories fade by never winning recall".

This module is PURE and deterministic: it computes a *plan* (a list of actions).
It never calls NotebookLM itself — the caller (nlmx adapter / MCP tool) executes the
plan, so the destructive part (eviction) always passes through the human/confirm gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .quality import (
    QualityWeights,
    SourceFeatures,
    _content_terms,
    authority_score,
    depth_score,
    recency_score,
    relevance_score,
)


class Action(str, Enum):
    CREATE = "create"
    MERGE = "merge"      # candidate merges into / is dropped for an existing source
    SKIP = "skip"
    EVICT = "evict"      # existing source proposed for removal
    KEEP = "keep"


@dataclass
class SourceStat:
    """Per-source running state persisted across evolution cycles (in the ledger)."""
    id: str
    score: float                # last observed composite score 0..1
    strikes: int = 0            # consecutive observations below the floor
    observations: int = 0
    last_score: float = 0.0


@dataclass
class EvolutionPolicy:
    quality_floor: float = 0.35      # below this a source is "weak"
    keep_budget: int = 25            # max sources a notebook should hold
    decay: float = 0.9               # per-observation multiplier when not refreshed
    evict_after_strikes: int = 3     # weak this many times in a row → propose evict
    merge_similarity: float = 0.82   # title+content term overlap to call two sources dupes
    refresh_delta: float = 0.05      # score must rise this much to count as "refreshed"
    protect_sole_cover: bool = True  # never evict a source that is the SOLE cover for a
    #                                  question term (ablation coverage guard) — a weak
    #                                  score can't delete the only source of a sub-topic


@dataclass
class PlanItem:
    action: Action
    source_id: str
    score: float
    reason: str
    merges_with: str | None = None

    def as_dict(self) -> dict:
        d = {"action": self.action.value, "source_id": self.source_id,
             "score": round(self.score, 4), "reason": self.reason}
        if self.merges_with:
            d["merges_with"] = self.merges_with
        return d


@dataclass
class EvolutionPlan:
    notebook_id: str
    items: list[PlanItem] = field(default_factory=list)
    kept: int = 0
    creates: int = 0
    merges: int = 0
    skips: int = 0
    evicts: int = 0

    def as_dict(self) -> dict:
        return {
            "notebook_id": self.notebook_id,
            "summary": {"kept": self.kept, "create": self.creates, "merge": self.merges,
                        "skip": self.skips, "evict": self.evicts},
            "items": [i.as_dict() for i in self.items],
        }


def _score_one(s: SourceFeatures, question: str, w: QualityWeights) -> float:
    qterms = _content_terms(question)
    return (
        w.authority * authority_score(s, w)
        + w.depth * depth_score(s, w)
        + w.recency * recency_score(s, w)
        + w.relevance * relevance_score(s, qterms, w.term_expansions)
    ) / max(1e-9, (w.authority + w.depth + w.recency + w.relevance))


def _similarity(a: SourceFeatures, b: SourceFeatures,
                require_domain_match: bool = False) -> float:
    """Jaccard over title+content terms; domain match is a strong prior.

    With require_domain_match=True (used for de-duplicating EXISTING sources), two
    sources on DIFFERENT registrable domains are never called duplicates however
    similar their text — so independent corroboration across domains is preserved,
    and only true republications (same URL/domain) collapse.
    """
    same_domain = bool(a.registrable) and a.registrable == b.registrable
    if require_domain_match and not same_domain:
        return 0.0
    ta = _content_terms(f"{a.title} {a.content}")
    tb = _content_terms(f"{b.title} {b.content}")
    if not ta or not tb:
        # fall back to domain identity when we have no text
        return 1.0 if same_domain else 0.0
    jac = len(ta & tb) / len(ta | tb)
    if same_domain:
        jac = min(1.0, jac + 0.3)  # same domain nudges toward "duplicate"
    return jac


def plan_evolution(
    notebook_id: str,
    existing: list[SourceFeatures],
    candidates: list[SourceFeatures] | None = None,
    stats: dict[str, SourceStat] | None = None,
    question: str = "",
    policy: EvolutionPolicy | None = None,
    weights: QualityWeights | None = None,
) -> tuple[EvolutionPlan, dict[str, SourceStat]]:
    """Compute an evolution plan for a notebook. Returns (plan, updated_stats).

    `existing`   — sources currently in the notebook.
    `candidates` — new sources proposed for addition (e.g. from a fresh research run).
    `stats`      — per-source running state from prior cycles (drives decay/eviction).
    Nothing is executed here; the caller runs the plan with the confirm gate on evicts.
    """
    pol = policy or EvolutionPolicy()
    w = (weights or QualityWeights()).normalized()
    stats = dict(stats or {})
    candidates = candidates or []
    plan = EvolutionPlan(notebook_id=notebook_id)

    # 1) Re-score existing sources and update decay/strikes.
    scored_existing: list[tuple[SourceFeatures, float]] = []
    for s in existing:
        raw = _score_one(s, question, w)
        st = stats.get(s.id, SourceStat(id=s.id, score=raw))
        refreshed = raw >= st.last_score + pol.refresh_delta
        effective = raw if refreshed else raw * (pol.decay ** st.observations)
        st.observations += 1
        st.last_score = raw
        st.score = effective
        st.strikes = 0 if effective >= pol.quality_floor else st.strikes + 1
        stats[s.id] = st
        scored_existing.append((s, effective))

    # 2) EVICT existing sources that have been weak too many times in a row,
    #    and MERGE existing sources that duplicate a stronger existing source.
    scored_existing.sort(key=lambda t: t[1], reverse=True)  # strongest first

    # Ablation coverage guard: find sources that are the SOLE cover for a question
    # term — removing one would strip a sub-topic the rest of the set cannot answer.
    # These are protected from eviction (strike- and budget-based) regardless of score,
    # so a weak standalone score never deletes the only source of a sub-topic.
    qterms = _content_terms(question)
    sole_cover: dict[str, list[str]] = {}
    if pol.protect_sole_cover and qterms:
        exp = w.term_expansions
        term_coverers: dict[str, list[str]] = {}
        for s in existing:
            s_terms = _content_terms(f"{s.title} {s.content}")
            for t in qterms:
                if t in s_terms or (exp.get(t) and (exp[t] & s_terms)):
                    term_coverers.setdefault(t, []).append(s.id)
        for t, ids in term_coverers.items():
            if len(ids) == 1:
                sole_cover.setdefault(ids[0], []).append(t)
    survivors: list[tuple[SourceFeatures, float]] = []
    for s, eff in scored_existing:
        st = stats[s.id]
        # merge away a pre-existing duplicate of an already-surviving stronger source
        dup_of = next((k for k, _ in survivors
                       if _similarity(s, k, require_domain_match=True) >= pol.merge_similarity),
                      None)
        if dup_of is not None:
            plan.items.append(PlanItem(
                Action.MERGE, s.id, eff,
                f"duplicate of existing source {dup_of.id} "
                f"(similarity ≥ {pol.merge_similarity})", merges_with=dup_of.id))
            plan.merges += 1
            continue
        if st.strikes >= pol.evict_after_strikes:
            if s.id in sole_cover:
                plan.items.append(PlanItem(
                    Action.KEEP, s.id, eff,
                    f"weak (score {eff:.2f}) but SOLE cover for "
                    f"{sole_cover[s.id]} — coverage guard prevents eviction"))
                plan.kept += 1
                # NOTE: intentionally NOT added to `survivors` — it already has its
                # KEEP item; routing it through the budget step would emit a 2nd item.
                continue
            plan.items.append(PlanItem(
                Action.EVICT, s.id, eff,
                f"weak (score {eff:.2f} < floor {pol.quality_floor}) for "
                f"{st.strikes} consecutive cycles — stale / no fresh content"))
            plan.evicts += 1
        else:
            survivors.append((s, eff))

    # 3) Triage candidates: MERGE dupes, SKIP weak-and-redundant, else CREATE.
    keep_pool = list(survivors)
    created_ids: set[str] = set()
    for c in candidates:
        c_score = _score_one(c, question, w)
        dup = None
        for s, _ in keep_pool:
            if _similarity(c, s) >= pol.merge_similarity:
                dup = s
                break
        if dup is not None:
            # keep the higher-scoring of the pair; the candidate merges away either way
            plan.items.append(PlanItem(
                Action.MERGE, c.id, c_score,
                f"duplicate of existing source {dup.id} "
                f"(similarity ≥ {pol.merge_similarity})", merges_with=dup.id))
            plan.merges += 1
            continue
        if c_score < pol.quality_floor:
            plan.items.append(PlanItem(
                Action.SKIP, c.id, c_score,
                f"below quality floor ({c_score:.2f} < {pol.quality_floor}) and not novel"))
            plan.skips += 1
            continue
        created_ids.add(c.id)
        keep_pool.append((c, c_score))
        stats.setdefault(c.id, SourceStat(id=c.id, score=c_score, last_score=c_score,
                                          observations=1))

    # 4) BUDGET: rank the pool; the top keep_budget survive, the rest are dropped.
    #    A source gets exactly ONE plan item: CREATE (new, in budget), KEEP (existing,
    #    in budget), SKIP (new but over budget — never create then evict), or EVICT
    #    (existing but over budget).
    keep_pool.sort(key=lambda t: t[1], reverse=True)
    in_budget = keep_pool[:pol.keep_budget]
    over_budget = keep_pool[pol.keep_budget:]

    for s, eff in in_budget:
        if s.id in created_ids:
            plan.items.append(PlanItem(Action.CREATE, s.id, eff, "novel and above floor"))
            plan.creates += 1
        else:
            plan.items.append(PlanItem(Action.KEEP, s.id, eff, "within budget, above floor"))
            plan.kept += 1

    for s, eff in over_budget:
        if s.id in created_ids:
            plan.items.append(PlanItem(
                Action.SKIP, s.id, eff,
                f"above floor but over keep_budget ({pol.keep_budget}); not added"))
            plan.skips += 1
        else:
            if s.id in sole_cover:
                plan.items.append(PlanItem(
                    Action.KEEP, s.id, eff,
                    f"over keep_budget ({pol.keep_budget}) but SOLE cover for "
                    f"{sole_cover[s.id]} — coverage guard keeps it"))
                plan.kept += 1
                continue
            plan.items.append(PlanItem(
                Action.EVICT, s.id, eff,
                f"over keep_budget ({pol.keep_budget}); lowest-ranked, budgeted out"))
            plan.evicts += 1

    return plan, stats
