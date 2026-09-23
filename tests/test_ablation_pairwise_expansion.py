"""Pairwise redundancy + term-expansion (semantic-lite) tests.

Two capabilities single-source leave-one-out and lexical matching miss:
  * find_redundant_pairs — detects interchangeable source pairs (same question-term
    coverage) that LOO can't see, and names the weaker as the drop candidate;
  * QualityWeights.term_expansions — a configured synonym set lets a source count as
    covering a question term via a synonym, fixing the lexical-relevance 0.0 case.
Offline/deterministic.
"""
from notelab.research.quality import (
    SourceFeatures, QualityWeights, relevance_score, score_sources, _content_terms,
)
from notelab.research.ablation import find_redundant_pairs, ablate_sources


def _s(i, dom, text, n=120):
    return SourceFeatures(id=i, url=f"https://{dom}/{i}", content=(text + " ") * n)


Q = "quantum error correction decoder"


# --- pairwise redundancy ---------------------------------------------------------

def test_find_redundant_pairs_detects_interchangeable_twins():
    # A and B cover the SAME terms; A (arxiv) scores higher -> B is the drop candidate.
    a = _s("A", "arxiv.org", "quantum error correction")
    b = _s("B", "some-blog.example", "quantum error correction")
    c = _s("C", "nature.com", "decoder")  # covers a different term -> not in a pair
    pairs = find_redundant_pairs([a, b, c], Q)
    assert len(pairs) == 1
    p = pairs[0]
    assert p.keep_id == "A" and p.drop_id == "B"
    assert set(p.shared_terms) == {"quantum", "error", "correction"}


def test_no_redundant_pair_when_coverage_differs():
    a = _s("A", "arxiv.org", "quantum error correction")
    c = _s("C", "nature.com", "decoder")
    assert find_redundant_pairs([a, c], Q) == []


def test_single_loo_misses_pair_that_pairwise_catches():
    # Demonstrates WHY pairwise is needed: leave-one-out marks neither twin as
    # load-bearing (removing one, the other still covers), so only the pairwise pass
    # surfaces the redundancy.
    a = _s("A", "arxiv.org", "quantum error correction")
    b = _s("B", "nature.com", "quantum error correction")
    rep = ablate_sources([a, b], Q)
    by = {v.id: v for v in rep.per_source}
    assert by["A"].coverage_delta == 0.0 and by["B"].coverage_delta == 0.0  # LOO sees nothing
    assert len(find_redundant_pairs([a, b], Q)) == 1                        # pairwise does


# --- term expansion (semantic-lite) ---------------------------------------------

def test_expansion_lets_synonym_cover_a_term():
    qterms = _content_terms(Q)
    syn = SourceFeatures(id="X", content="surface codes mwpm belief-propagation " * 40)
    # without expansion: 'decoder' not present -> that term uncovered
    assert relevance_score(syn, qterms) < 1.0
    # with expansion mapping decoder -> {mwpm, ...}: now covered
    exp = {"decoder": {"mwpm"}, "quantum": {"surface"}, "error": {"codes"},
           "correction": {"codes"}}
    r = relevance_score(syn, qterms, exp)
    assert r > relevance_score(syn, qterms)  # expansion strictly helps


def test_expansion_flows_through_score_sources_and_ablation():
    syn = SourceFeatures(id="X", url="https://arxiv.org/x",
                         content="surface codes mwpm " * 60)
    w = QualityWeights(term_expansions={"decoder": {"mwpm"}, "quantum": {"surface"}})
    base = score_sources([syn], Q).quality_score
    with_exp = score_sources([syn], Q, weights=w).quality_score
    assert with_exp >= base  # relevance component rises, never falls
