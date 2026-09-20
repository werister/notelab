"""Quality scorer tests — fully offline, fixture source sets."""
from datetime import datetime, timedelta, timezone

from notelab.research.quality import (
    QualityWeights,
    SourceFeatures,
    authority_score,
    depth_score,
    diversity_score,
    recency_score,
    relevance_score,
    score_sources,
)

W = QualityWeights()


def _src(**kw):
    kw.setdefault("id", kw.get("url", "x"))
    return SourceFeatures(**kw)


def test_authority_prefers_primary_sources():
    gov = _src(url="https://www.nih.gov/study")
    arxiv = _src(url="https://arxiv.org/abs/1234")
    farm = _src(url="https://top10-best-deals.example.com/listicle")
    medium = _src(url="https://medium.com/@someone/post")
    assert authority_score(gov, W) > 0.9
    assert authority_score(arxiv, W) > 0.9
    assert authority_score(farm, W) < 0.3
    assert authority_score(medium, W) < authority_score(gov, W)


def test_authority_override_wins():
    w = QualityWeights(authority_overrides={"myintranet.local": 1.0})
    s = _src(url="https://myintranet.local/doc")
    assert authority_score(s, w) == 1.0


def test_depth_rewards_substance_saturates():
    thin = _src(content="short")
    thick = _src(content="x" * 5000)
    assert depth_score(thin, W) < depth_score(thick, W)
    assert depth_score(thick, W) <= 1.0
    # unknown length is neutral, not zero
    assert 0.3 < depth_score(_src(), W) < 0.6


def test_recency_decays_with_age():
    now = datetime.now(timezone.utc)
    fresh = _src(published=now - timedelta(days=1))
    old = _src(published=now - timedelta(days=3650))
    assert recency_score(fresh, W) > 0.9
    assert recency_score(old, W) < 0.1
    assert recency_score(_src(), W) == 0.5  # undated neutral


def test_relevance_matches_question_terms():
    q_terms = {"quantum", "computing", "error", "correction"}
    on = _src(title="Quantum error correction", content="quantum computing error correction codes")
    off = _src(title="Sourdough bread", content="flour water salt yeast baking")
    assert relevance_score(on, q_terms) > relevance_score(off, q_terms)
    assert relevance_score(off, q_terms) == 0.0


def test_diversity_punishes_single_domain():
    same = [_src(url=f"https://oneblog.com/{i}") for i in range(6)]
    varied = [_src(url=f"https://site{i}.com/x") for i in range(6)]
    assert diversity_score(same) < 0.2
    assert diversity_score(varied) > 0.8
    assert diversity_score([_src(url="https://a.com/1")]) == 0.0


def test_composite_ranks_a_good_set_above_a_bad_set():
    now = datetime.now(timezone.utc)
    good = [
        _src(url="https://arxiv.org/abs/1", content="quantum error correction " * 200,
             published=now - timedelta(days=30)),
        _src(url="https://www.nih.gov/2", content="decoherence surface codes " * 200,
             published=now - timedelta(days=60)),
        _src(url="https://nature.com/3", content="fault tolerant quantum " * 200,
             published=now - timedelta(days=90)),
    ]
    bad = [
        _src(url="https://top10-cheap-deals.com/a", content="buy now", published=now - timedelta(days=4000)),
        _src(url="https://top10-cheap-deals.com/b", content="click here", published=now - timedelta(days=4000)),
        _src(url="https://medium.com/@x/c", content="my opinion"),
    ]
    q = "quantum error correction fault tolerant"
    good_report = score_sources(good, q)
    bad_report = score_sources(bad, q)
    assert good_report.quality_score > bad_report.quality_score
    assert good_report.quality_score > 0.5
    assert bad_report.quality_score < 0.45


def test_empty_source_set_scores_zero_not_crash():
    r = score_sources([], "anything")
    assert r.quality_score == 0.0
    assert r.n_sources == 0


def test_report_decomposes_into_signals():
    r = score_sources([_src(url="https://arxiv.org/1", content="deep learning " * 100)], "deep learning")
    d = r.as_dict()
    assert set(d["signal_means"]) == {"authority", "depth", "recency", "relevance", "diversity"}
    assert set(d["weights"]) == {"authority", "diversity", "recency", "depth", "relevance"}
    assert d["per_source"][0]["domain"] == "arxiv.org"
