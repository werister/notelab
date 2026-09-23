"""Coverage-guard tests for plan_evolution — the ablation insight wired into evict.

A weak source that is the SOLE cover for a question term must NOT be evicted
(protect_sole_cover), because standalone score would otherwise delete the only
source of a sub-topic. A weak source whose terms are covered by others is still
evicted as before. Offline/deterministic.
"""
from notelab.research.evolve import (
    plan_evolution, EvolutionPolicy, SourceStat, Action,
)
from notelab.research.quality import SourceFeatures


def _s(i, dom, text, n=120):
    return SourceFeatures(id=i, url=f"https://{dom}/{i}", content=(text + " ") * n)


Q = "quantum error correction decoder"


def _weak_stat(id_):
    # already at the strike threshold so this source is evict-eligible next cycle
    return SourceStat(id=id_, score=0.1, strikes=3, observations=5, last_score=0.1)


def test_sole_cover_weak_source_is_kept_not_evicted():
    # W is weak (spam domain + minimal content -> below floor) AND the only source
    # covering 'decoder'; R is weak but redundant (covered by strong S).
    strong = _s("S", "arxiv.org", "quantum error correction")
    sole = SourceFeatures(id="W", url="https://top10-deals.com/x", content="decoder")  # below floor, sole 'decoder'
    redundant = SourceFeatures(id="R", url="https://top10-deals.com/y", content="quantum")  # below floor, terms covered by S
    existing = [strong, sole, redundant]
    stats = {"W": _weak_stat("W"), "R": _weak_stat("R"), "S": SourceStat(id="S", score=0.9)}

    plan, _ = plan_evolution("nb", existing, question=Q,
                             stats=stats, policy=EvolutionPolicy())
    by = {it.source_id: it for it in plan.items}
    assert by["W"].action == Action.KEEP, ("sole-cover weak source must be kept", by["W"].reason)
    assert "SOLE cover" in by["W"].reason
    # R is weak and redundant -> still evicted (or merged), never protected
    assert by["R"].action in (Action.EVICT, Action.MERGE)


def test_guard_can_be_disabled():
    sole = SourceFeatures(id="W", url="https://top10-deals.com/x", content="decoder")  # below floor
    existing = [_s("S", "arxiv.org", "quantum error correction"), sole]
    stats = {"W": _weak_stat("W"), "S": SourceStat(id="S", score=0.9)}
    plan, _ = plan_evolution("nb", existing, question=Q, stats=stats,
                             policy=EvolutionPolicy(protect_sole_cover=False))
    by = {it.source_id: it for it in plan.items}
    # with the guard off, the weak sole-cover source is evicted as before
    assert by["W"].action == Action.EVICT
