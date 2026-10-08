"""Weight-preset tests — offline."""
import pytest

from notelab.research.quality import PRESETS, QualityWeights, preset, score_sources
from notelab.research.quality import SourceFeatures


def test_presets_exist():
    assert set(PRESETS) >= {"news", "lit_review", "docs", "balanced", "project_kickstart"}
    assert all(isinstance(w, QualityWeights) for w in PRESETS.values())


def test_project_kickstart_preset_rewards_a_diverse_mix_of_kinds():
    """The project_kickstart preset is the vision in one knob: a question answered by
    a diverse mix (paper + repo + video + discussion + docs) should score clearly above
    the same number of sources that are all one kind. The old balanced preset, which
    leans less on kind spread, shows a smaller gap — proving the preset, not just the
    scorer, is what favours a diverse starting library."""
    def _mk(urls):
        return [SourceFeatures(id=u, url=u, title="", content="starting a project step by step " * 50)
                for u in urls]
    diverse = _mk([
        "https://arxiv.org/abs/1",                       # paper
        "https://github.com/org/repo",                   # repo
        "https://www.youtube.com/watch?v=abc",           # video
        "https://www.reddit.com/r/x/comments/y",         # discussion
        "https://docs.python.org/3/",                    # docs
    ])
    mono = _mk([f"https://blog{i}.example/post" for i in range(5)])  # all web
    q = "how to start the project step by step"
    kick_diverse = score_sources(diverse, q, preset("project_kickstart")).quality_score
    kick_mono = score_sources(mono, q, preset("project_kickstart")).quality_score
    assert kick_diverse > kick_mono
    # the kickstart preset should separate them harder than balanced does
    bal_diverse = score_sources(diverse, q, preset("balanced")).quality_score
    bal_mono = score_sources(mono, q, preset("balanced")).quality_score
    assert (kick_diverse - kick_mono) > (bal_diverse - bal_mono)


def test_news_favors_recency_over_depth():
    n = PRESETS["news"].normalized()
    assert n.recency > n.depth


def test_lit_review_favors_depth_over_recency():
    lr = PRESETS["lit_review"].normalized()
    assert lr.depth > lr.recency


def test_preset_lookup_and_error():
    assert preset("news") is PRESETS["news"]
    with pytest.raises(KeyError):
        preset("nonexistent")


def test_preset_changes_ranking_direction():
    # a fresh-but-shallow source vs a deep-but-old one
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    fresh = [SourceFeatures(id="f", url="https://a.gov/1", content="short note",
                            published=now - timedelta(days=2))]
    deep_old = [SourceFeatures(id="d", url="https://b.gov/1", content="x" * 6000,
                               published=now - timedelta(days=1500))]
    q = "topic"
    # news weighting should rate the fresh source's recency highly
    fresh_news = score_sources(fresh, q, preset("news")).signal_means["recency"]
    # lit_review should rate the deep source's depth highly
    deep_lit = score_sources(deep_old, q, preset("lit_review")).signal_means["depth"]
    assert fresh_news > 0.8
    assert deep_lit > 0.8
