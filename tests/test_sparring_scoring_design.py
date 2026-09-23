"""Design-characterization tests for how sparring SCORES families.

These pin the *current* behavior of the deterministic composite so any future
tuning is a deliberate, measured change rather than an accidental drift. Each
assertion is backed by an offline probe (see the notes) — no network/keys.
"""
from notelab.research.quality import (
    SourceFeatures, score_sources, relevance_score, _content_terms,
)


def _src(i, dom, word, n=200):
    return SourceFeatures(id=str(i), url=f"https://{dom}/{i}", content=(word + " ") * n)


def test_few_excellent_beats_many_mediocre():
    """A family returning 2 authoritative sources must beat one dumping 6 mid-tier
    same-domain-shaped sources. Verified: 0.699 vs 0.354. This is the property that
    stops a family gaming the ranking by returning volume over quality."""
    q = "quantum error correction thresholds"
    few = score_sources([_src(1, "arxiv.org", "threshold"),
                         _src(2, "nature.com", "surface-code")], q)
    many = score_sources([_src(i, f"blog{i}.example.com", "threshold", n=60)
                          for i in range(3, 9)], q)
    assert few.quality_score > many.quality_score
    # the diversity term punishes near-identical low-authority hosts
    assert many.diversity < few.diversity


def test_relevance_is_lexical_documented_limitation():
    """KNOWN LIMITATION: relevance_score is lexical term-overlap only. A genuinely
    relevant source that uses SYNONYMS scores 0 on relevance (weighted 0.20). This
    test documents the current behavior so a future semantic-relevance upgrade is a
    conscious, reviewed change — not a silent score shift across the ledger."""
    q = "quantum error correction thresholds"
    qterms = _content_terms(q)
    exact = SourceFeatures(id="1", content="quantum error correction thresholds " * 50)
    synonym = SourceFeatures(id="2",
                             content="surface codes fault tolerance logical qubit " * 50)
    assert relevance_score(exact, qterms) == 1.0
    assert relevance_score(synonym, qterms) == 0.0  # lexical miss despite being on-topic


def test_empty_source_set_scores_zero_not_crash():
    r = score_sources([], "any question")
    assert r.quality_score == 0.0
    assert r.n_sources == 0
