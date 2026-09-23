"""Greedy N-way redundancy + lightweight fast/deep lane tests. Offline/deterministic."""
from notelab.research.quality import SourceFeatures
from notelab.research.ablation import greedy_prune, quick_assess


def _s(i, dom, text, n=120):
    return SourceFeatures(id=i, url=f"https://{dom}/{i}", content=(text + " ") * n)


Q = "quantum error correction decoder"


def test_greedy_collapses_three_way_redundancy_to_strongest():
    # A, B, C all cover the SAME terms; D uniquely covers 'decoder'.
    a = _s("A", "arxiv.org", "quantum error correction")
    b = _s("B", "some-blog.example", "quantum error correction")
    c = _s("C", "other-blog.example", "quantum error correction")
    d = _s("D", "nature.com", "decoder")
    res = greedy_prune([a, b, c, d], Q)
    # coverage preserved; the redundant twins collapse, D (sole 'decoder' cover) stays
    assert res.kept_coverage == res.full_coverage
    assert "D" in res.kept_ids
    # at least the two weakest redundant blogs are dropped
    assert {"B", "C"}.issubset({d.dropped_id for d in res.dropped})
    # the strongest of the redundant group (A, arxiv) is retained
    assert "A" in res.kept_ids


def test_greedy_respects_min_keep():
    a = _s("A", "arxiv.org", "quantum error correction")
    b = _s("B", "blog.example", "quantum error correction")
    res = greedy_prune([a, b], Q, min_keep=2)
    assert len(res.kept_ids) == 2  # never prunes below min_keep


def test_quick_assess_fast_good_when_covered_and_strong():
    a = _s("A", "arxiv.org", "quantum error correction decoder")
    r = quick_assess([a], Q, mode="fast")
    assert r["mode"] == "fast"
    assert r["coverage"] == 1.0
    assert r["verdict"] == "good"
    assert r["escalate_to_deep"] is False


def test_quick_assess_fast_flags_thin_on_missing_coverage():
    a = _s("A", "arxiv.org", "quantum error correction")  # no 'decoder'
    r = quick_assess([a], Q, mode="fast")
    assert r["verdict"] == "thin"
    assert "decoder" in r["coverage_gaps"]
    assert r["escalate_to_deep"] is True


def test_quick_assess_fast_flags_bloated():
    many = [_s(str(i), "arxiv.org", "quantum error correction decoder") for i in range(12)]
    r = quick_assess(many, Q, mode="fast")
    assert r["verdict"] == "bloated"        # full coverage but too many sources
    assert r["escalate_to_deep"] is True


def test_quick_assess_deep_returns_full_pipeline():
    srcs = [_s("A", "arxiv.org", "quantum error correction"),
            _s("B", "blog.example", "quantum error correction"),
            _s("C", "nature.com", "decoder")]
    r = quick_assess(srcs, Q, mode="deep")
    assert r["mode"] == "deep"
    assert "ablation" in r and "redundant_pairs" in r and "greedy_prune" in r


def test_quick_assess_rejects_unknown_mode():
    import pytest
    a = _s("A", "arxiv.org", "quantum error correction decoder")
    with pytest.raises(ValueError):
        quick_assess([a], Q, mode="quick")


def test_quick_assess_mode_is_case_insensitive():
    a = _s("A", "arxiv.org", "quantum error correction decoder")
    assert quick_assess([a], Q, mode="FAST")["mode"] == "fast"
    assert quick_assess([a], Q, mode="Deep")["mode"] == "deep"


def test_quick_assess_fast_unassessable_on_empty_question():
    a = _s("A", "arxiv.org", "quantum error correction decoder")
    r = quick_assess([a], "", mode="fast")
    assert r["verdict"] == "unassessable"
