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


def test_domain_diversity_punishes_single_domain():
    from notelab.research.quality import domain_diversity_score
    same = [_src(url=f"https://oneblog.com/{i}") for i in range(6)]
    varied = [_src(url=f"https://site{i}.com/x") for i in range(6)]
    assert domain_diversity_score(same) < 0.2
    assert domain_diversity_score(varied) > 0.8
    assert domain_diversity_score([_src(url="https://a.com/1")]) == 0.0


def test_blended_diversity_rewards_a_mix_of_kinds_over_many_blogs():
    """The core NoteLab premise: a paper + repo + video + discussion is MORE diverse
    than six blogs on six distinct domains. The default blended diversity_score (which
    counts research-KIND spread, not just hostnames) must rank the mixed set higher,
    even though the blog set has strictly more distinct domains."""
    six_blogs = [_src(url=f"https://site{i}.com/x") for i in range(6)]       # all kind=web
    mixed = [
        _src(url="https://arxiv.org/abs/1"),                                  # paper
        _src(url="https://github.com/org/repo"),                              # repo
        _src(url="https://www.youtube.com/watch?v=abc"),                      # video
        _src(url="https://www.reddit.com/r/x/comments/y"),                    # discussion
        _src(url="https://docs.python.org/3/lib"),                            # docs
    ]
    assert diversity_score(mixed) > diversity_score(six_blogs)
    # the gap is entirely the KIND signal: with kind-weighting off both sets score on
    # domain spread alone, where the six blogs (6 domains) at least tie the mix (5),
    # so the mix does NOT lead. Turning kind-weighting on is what flips it.
    assert diversity_score(mixed, type_weight=0.0) <= diversity_score(six_blogs, type_weight=0.0)
    assert diversity_score([_src(url="https://a.com/1")]) == 0.0


def test_type_diversity_signal_counts_distinct_kinds():
    from notelab.research.quality import type_diversity_score
    one_kind = [_src(url=f"https://blog{i}.com/x") for i in range(5)]         # all web
    many_kinds = [
        _src(url="https://arxiv.org/abs/1"),
        _src(url="https://github.com/o/r"),
        _src(url="https://youtu.be/abc"),
        _src(url="https://news.ycombinator.com/item?id=1"),
        _src(url="https://docs.python.org/3/"),
    ]
    assert type_diversity_score(one_kind) == 0.0
    assert type_diversity_score(many_kinds) == 1.0


def test_classify_source_type_covers_the_core_kinds():
    from notelab.research.quality import classify_source_type
    assert classify_source_type("https://arxiv.org/abs/2401.00001") == "paper"
    assert classify_source_type("https://github.com/python/cpython") == "repo"
    assert classify_source_type("https://www.youtube.com/watch?v=xyz") == "video"
    assert classify_source_type("https://youtu.be/xyz") == "video"
    assert classify_source_type("https://www.reddit.com/r/MachineLearning/comments/x") == "discussion"
    assert classify_source_type("https://news.ycombinator.com/item?id=1") == "discussion"
    assert classify_source_type("https://docs.python.org/3/library/os.html") == "docs"
    assert classify_source_type("https://en.wikipedia.org/wiki/RAG") == "reference"
    assert classify_source_type("https://some-random-blog.example/post") == "web"
    # explicit override wins over URL inference
    s = _src(url="https://github.com/o/r", title="")
    s.source_type = "paper"
    assert s.kind == "paper"


def test_kind_weights_let_the_user_own_what_they_value():
    """The 'user owns what they value' principle: the SAME sources score differently
    under different per-kind value profiles, and a kind weighted 0.0 contributes
    nothing to the composite or to kind-diversity."""
    from notelab.research.quality import QualityWeights
    srcs = [
        _src(url="https://arxiv.org/abs/1", content="rag theory " * 150),            # paper
        _src(url="https://reddit.com/r/x/y", content="rag community gotchas " * 120),  # discussion
        _src(url="https://youtube.com/watch?v=z", content="rag step by step " * 120),  # video
    ]
    q = "how to build a rag app"
    # a user who values ONLY papers: forum+video zeroed out
    papers_only = score_sources(
        srcs, q, QualityWeights(kind_weights={"paper": 1.0, "discussion": 0.0, "video": 0.0})
    ).as_dict()
    # a user who values forums/videos over papers
    forum_first = score_sources(
        srcs, q, QualityWeights(kind_weights={"discussion": 1.0, "video": 1.0, "paper": 0.1})
    ).as_dict()
    # the two profiles must produce different composites on identical sources
    assert papers_only["quality_score"] != forum_first["quality_score"]
    # with forum+video valued 0, only the paper kind counts toward diversity
    assert papers_only["type_diversity"] < 0.5
    # neutral default (no kind_weights) still treats all kinds equally -> full spread
    neutral = score_sources(srcs, q, QualityWeights()).as_dict()
    assert neutral["type_diversity"] == 1.0


def test_kind_weights_absent_reproduces_neutral_mean_exactly():
    """No kind_weights configured must be a pure no-op: identical to the plain mean,
    so existing scores never move unless the user opts in."""
    from notelab.research.quality import QualityWeights
    srcs = [
        _src(url="https://arxiv.org/abs/1", content="x " * 100),
        _src(url="https://blog.example/2", content="y " * 100),
    ]
    w_plain = QualityWeights()
    w_explicit_ones = QualityWeights(kind_weights={"paper": 1.0, "web": 1.0})
    a = score_sources(srcs, "topic", w_plain).quality_score
    b = score_sources(srcs, "topic", w_explicit_ones).quality_score
    assert abs(a - b) < 1e-9


def test_normalized_preserves_kind_weights_and_config():
    """normalized() must carry kind_weights (and every other config field) through —
    the dataclasses.replace fix. A regression here would silently drop user intent."""
    from notelab.research.quality import QualityWeights
    w = QualityWeights(kind_weights={"video": 0.0}, diversity_type_weight=0.8,
                       term_expansions={"a": {"b"}}, authority_overrides={"x.com": 0.9})
    n = w.normalized()
    assert n.kind_weights == {"video": 0.0}
    assert n.diversity_type_weight == 0.8
    assert n.term_expansions == {"a": {"b"}}
    assert n.authority_overrides == {"x.com": 0.9}
    assert abs((n.authority + n.diversity + n.recency + n.depth + n.relevance) - 1.0) < 1e-9


def test_blocked_botwall_video_scores_low_depth_and_loses_kind_credit():
    """A YouTube link imported as a 'Sign in to confirm you're not a bot' shell (seen
    live from NotebookLM Deep Research) is long but empty. It must NOT earn full depth,
    and must not count as a real 'video' kind for type-diversity — otherwise a notebook
    full of blocked videos looks deep and diverse while holding nothing."""
    from notelab.research.quality import (
        is_blocked_content, depth_score, type_diversity_score, QualityWeights,
    )
    botwall_text = ("Sign in Skip navigation Search with your voice "
                    "Sign in to confirm you're not a bot " * 300)
    blocked = _src(url="https://www.youtube.com/watch?v=x", title="RAG course - YouTube",
                   content=botwall_text)
    real = _src(url="https://www.youtube.com/watch?v=y", title="RAG course - YouTube",
                content="in this course we build retrieval augmented generation " * 300)
    assert is_blocked_content(botwall_text) is True
    assert blocked.is_blocked is True and real.is_blocked is False
    # blocked video gets floored depth, real transcript gets full depth
    assert depth_score(blocked, QualityWeights()) <= 0.15
    assert depth_score(real, QualityWeights()) > 0.9
    # a blocked video + a paper spans only ONE real kind (paper); the blocked video
    # doesn't add 'video' variety.
    paper = _src(url="https://arxiv.org/abs/1")
    assert type_diversity_score([blocked, paper]) < type_diversity_score([real, paper])


def test_is_blocked_content_needs_multiple_markers():
    """A single incidental mention of 'sign in' in real content must not trip the
    blocked heuristic — it requires several nav-chrome markers together."""
    from notelab.research.quality import is_blocked_content
    assert is_blocked_content("You can sign in to the app to save your work.") is False
    assert is_blocked_content("") is False


def test_video_and_discussion_not_penalized_on_authority():
    """A YouTube walkthrough and a Q&A/discussion host should sit at the neutral 0.5
    authority baseline, not be dragged to low-authority territory — authority is the
    wrong axis for a platform source; it earns its place via relevance and kind
    diversity instead."""
    from notelab.research.quality import authority_score, QualityWeights
    w = QualityWeights()
    video = _src(url="https://www.youtube.com/watch?v=abc")
    quora = _src(url="https://www.quora.com/q")   # quora is in the low-authority list
    assert authority_score(video, w) == 0.5
    # quora classifies as discussion -> rescued to the 0.5 neutral baseline
    assert authority_score(quora, w) == 0.5


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


def test_report_exposes_kind_weight_so_composite_is_reconstructable():
    """Auditability (CONTRIBUTING): with non-neutral kind_weights the signal_means
    are a kind-WEIGHTED mean, so each source's effective kind_weight must appear in
    the report, else a reader cannot reproduce signal_means from per_source."""
    # A paper and a forum post; the user values papers and ignores discussion.
    paper = _src(url="https://arxiv.org/abs/1", content="x" * 5000, source_type="paper")
    forum = _src(url="https://reddit.com/r/x/1", content="y" * 5000,
                 source_type="discussion")
    w = QualityWeights(kind_weights={"paper": 1.0, "discussion": 0.0})
    report = score_sources([paper, forum], question="x", weights=w).as_dict()

    per = {s["id"]: s for s in report["per_source"]}
    # The multiplier is always present, so a reader never has to know an implicit default.
    assert per["https://arxiv.org/abs/1"]["kind_weight"] == 1.0
    assert per["https://reddit.com/r/x/1"]["kind_weight"] == 0.0

    # Reconstruct each weighted signal mean from the raw per-source signals + weights.
    for signal in ("authority", "depth", "recency", "relevance"):
        num = sum(s[signal] * s["kind_weight"] for s in report["per_source"])
        den = sum(s["kind_weight"] for s in report["per_source"])
        expected = num / den if den else 0.0
        assert abs(report["signal_means"][signal] - expected) < 1e-4, signal


def test_neutral_kind_weights_still_report_the_multiplier():
    """With no kind_weights set the multiplier is still emitted (1.0), so the report
    schema does not change shape depending on the user's config."""
    s = _src(url="https://arxiv.org/abs/1", content="x" * 2000)
    report = score_sources([s], question="x").as_dict()
    assert report["per_source"][0]["kind_weight"] == 1.0
