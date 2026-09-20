"""Weight-preset tests — offline."""
import pytest

from notelab.research.quality import PRESETS, QualityWeights, preset, score_sources
from notelab.research.quality import SourceFeatures


def test_presets_exist():
    assert set(PRESETS) >= {"news", "lit_review", "docs", "balanced"}
    assert all(isinstance(w, QualityWeights) for w in PRESETS.values())


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
