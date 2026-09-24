"""Leave-one-out ablation tests — 'does this source actually add value?'

Verified locally: these pin the marginal-value verdicts that a standalone-score
evict rule gets WRONG — a low-authority sole-cover source is load-bearing, a
high-authority source with a redundant twin is not uniquely valuable, and pure
noise is cuttable. Offline/deterministic — no network, keys, or CLIs.
"""
from unittest.mock import patch

import pytest

from notelab.research.quality import SourceFeatures
from notelab.research.ablation import ablate_sources
from notelab.research import laya_judge
from notelab.research.laya_judge import LayaJudgeError, RedundancyJudgment


def _s(i, dom, text, n=120):
    return SourceFeatures(id=i, url=f"https://{dom}/{i}", content=(text + " ") * n)


Q = "quantum error correction surface code threshold decoder"


def _fixture():
    return [
        _s("A", "arxiv.org",  "quantum error correction surface code threshold"),
        _s("B", "nature.com", "quantum error correction surface code threshold"),
        _s("C", "some-lab-blog.org", "decoder algorithm belief propagation"),
        _s("D", "top10-quantum-deals.com", "buy cheap quantum stuff now discount", n=2),
    ]


def test_sole_cover_source_is_load_bearing_even_if_low_authority():
    rep = ablate_sources(_fixture(), Q)
    c = {v.id: v for v in rep.per_source}["C"]
    assert c.verdict == "load-bearing"
    assert "decoder" in c.sole_cover_terms
    assert c.coverage_delta > 0          # removing C loses question coverage
    assert "C" in rep.keep_locked
    assert "C" not in rep.cut_candidates


def test_pure_noise_source_is_cuttable():
    rep = ablate_sources(_fixture(), Q)
    d = {v.id: v for v in rep.per_source}["D"]
    assert d.verdict != "load-bearing"
    assert d.coverage_delta == 0.0
    assert "D" in rep.cut_candidates


def test_redundant_twin_loses_no_coverage_when_removed():
    # A and B cover identical terms; removing EITHER keeps full coverage.
    rep = ablate_sources(_fixture(), Q)
    by = {v.id: v for v in rep.per_source}
    assert by["A"].coverage_delta == 0.0
    assert by["B"].coverage_delta == 0.0


def test_full_coverage_and_report_shape():
    rep = ablate_sources(_fixture(), Q)
    assert rep.full_coverage == 1.0      # the set covers every question term
    assert rep.n_sources == 4
    d = rep.as_dict()
    assert d["per_source"][0]["verdict"] in {"load-bearing", "marginal", "redundant"}


def test_empty_set_is_safe():
    rep = ablate_sources([], Q)
    assert rep.n_sources == 0
    assert rep.per_source == []
    assert rep.cut_candidates == [] and rep.keep_locked == []


def _ambiguous_fixture():
    # 3 near-identical strong sources on the SAME topic -> all land in the
    # ambiguous zone at N=3 (mirrors the gap #8 diagnosis: composite_delta too
    # small to clear eps regardless of real quality, no single sole-cover term).
    return [
        _s("A", "arxiv.org", Q, n=50),
        _s("B", "nature.com", Q, n=50),
        _s("C", "some-lab.org", Q, n=50),
    ]


def test_use_laya_false_is_unchanged_from_before():
    rep = ablate_sources(_ambiguous_fixture(), Q, use_laya=False)
    assert all(v.verdict == "redundant" for v in rep.per_source)  # today's bug, unchanged
    assert all(v.judge_confidence is None for v in rep.per_source)


def test_use_laya_true_calls_judge_only_for_ambiguous_sources():
    fixture = [
        _s("A", "arxiv.org", "quantum error correction surface code threshold"),
        _s("B", "nature.com", "quantum error correction surface code threshold"),
        _s("C", "some-lab-blog.org", "decoder algorithm belief propagation"),  # sole cover
    ]
    with patch.object(laya_judge, "laya_available", return_value=True), \
         patch.object(laya_judge, "judge_redundancy") as mock_judge:
        mock_judge.return_value = RedundancyJudgment(
            verdict="redundant", confidence=0.6, probabilities={"redundant": 0.6, "load_bearing": 0.4})
        rep = ablate_sources(fixture, Q, use_laya=True)
    # C has a sole_cover_term -> load-bearing via the CLEAR signal, never touches Laya
    called_ids = [call.args[0].id for call in mock_judge.call_args_list]
    assert "C" not in called_ids
    by = {v.id: v for v in rep.per_source}
    assert by["C"].verdict == "load-bearing"
    assert by["C"].judge_confidence is None  # clear-signal path, Laya never consulted


def test_use_laya_true_applies_judge_verdict_and_confidence():
    with patch.object(laya_judge, "laya_available", return_value=True), \
         patch.object(laya_judge, "judge_redundancy") as mock_judge:
        mock_judge.return_value = RedundancyJudgment(
            verdict="load-bearing", confidence=0.83, probabilities={"load_bearing": 0.83, "redundant": 0.17})
        rep = ablate_sources(_ambiguous_fixture(), Q, use_laya=True)
    assert mock_judge.call_count == 3  # all 3 are ambiguous
    assert all(v.verdict == "load-bearing" for v in rep.per_source)
    assert all(v.judge_confidence == 0.83 for v in rep.per_source)


def test_use_laya_true_without_laya_mlx_raises_immediately():
    with patch.object(laya_judge, "laya_available", return_value=False):
        with pytest.raises(LayaJudgeError):
            ablate_sources(_ambiguous_fixture(), Q, use_laya=True)


def test_source_value_as_dict_includes_judge_confidence_only_when_set():
    rep_off = ablate_sources(_ambiguous_fixture(), Q, use_laya=False)
    assert "judge_confidence" not in rep_off.per_source[0].as_dict()

    with patch.object(laya_judge, "laya_available", return_value=True), \
         patch.object(laya_judge, "judge_redundancy") as mock_judge:
        mock_judge.return_value = RedundancyJudgment(
            verdict="redundant", confidence=0.55, probabilities={})
        rep_on = ablate_sources(_ambiguous_fixture(), Q, use_laya=True)
    d = rep_on.per_source[0].as_dict()
    assert d["judge_confidence"] == 0.55


def test_quick_assess_deep_mode_forwards_use_laya():
    from notelab.research.ablation import quick_assess

    with patch.object(laya_judge, "laya_available", return_value=True), \
         patch.object(laya_judge, "judge_redundancy") as mock_judge:
        mock_judge.return_value = RedundancyJudgment(
            verdict="load-bearing", confidence=0.7, probabilities={})
        result = quick_assess(_ambiguous_fixture(), Q, mode="deep", use_laya=True)
    assert mock_judge.call_count == 3
    assert all(s["verdict"] == "load-bearing" for s in result["ablation"]["per_source"])


def test_quick_assess_fast_mode_ignores_use_laya():
    """mode="fast" never calls ablate_sources at all -- use_laya must be a silent
    no-op there, not an error, since the fast lane's whole point is skipping the
    O(n^2) pipeline entirely."""
    from notelab.research.ablation import quick_assess

    result = quick_assess(_ambiguous_fixture(), Q, mode="fast", use_laya=True)
    assert result["mode"] == "fast"  # didn't raise, didn't touch ablation
