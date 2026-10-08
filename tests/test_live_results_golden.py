"""Golden regression test over REAL NotebookLM data (Step 4 of the source-value
work): 16 real sources pulled from a real research notebook via `nlmx source
content`, content truncated to the first 4000 chars per source to keep the
fixture small —
see docs/RESULTS-2026-09-23.md for the full-content live numbers and repro.

This pins the ACTUAL measured output of the engine on real (if truncated) research
content, so a future change to quality.py/ablation.py that silently shifts the
scores/verdicts gets caught here rather than only in synthetic fixtures. Offline
and deterministic — no network, no nlmx — the fixture is a frozen snapshot.
"""
import json
from pathlib import Path

from notelab.research.ablation import quick_assess
from notelab.research.quality import SourceFeatures, score_sources

FIXTURE = Path(__file__).parent / "fixtures" / "live_notebook_llm_judge_bias.json"

Q1 = "LLM-as-a-judge bias mitigation strategies"
Q2 = "central tendency bias in clinical ordinal scoring by multimodal LLM raters"


def _load_sources():
    fixture = json.loads(FIXTURE.read_text())
    return [SourceFeatures(id=s["id"], title=s["title"], url=s["url"], content=s["content"])
            for s in fixture]


def test_golden_broad_question_composite_and_verdict():
    """A broad, generic question phrase against a topically-coherent 16-source
    corpus: every source covers all 5 question terms somewhere in its own text, so
    coverage is trivially 1.0 and the fast lane's count-based bloated threshold
    fires. This is the documented lexical-coverage limitation in the wild, not a
    bug — see docs/RESULTS-2026-09-23.md."""
    sources = _load_sources()
    report = score_sources(sources, question=Q1)
    # 0.6439 (was 0.6716 before the kind-diversity signal): this corpus is a near
    # mono-culture of papers (type_mix ~14 paper / 2 web, type_diversity 0.167), so
    # the kind-aware diversity signal correctly scores it lower than the old
    # domain-only diversity did — a library of one kind is less diverse by design.
    assert round(report.quality_score, 4) == 0.6439
    assert report.as_dict()["type_mix"] == {"paper": 14, "web": 2}
    assert round(report.type_diversity, 4) == 0.1667

    fast = quick_assess(sources, Q1, mode="fast")
    assert fast["verdict"] == "bloated"
    assert fast["coverage"] == 1.0
    assert fast["escalate_to_deep"] is True


def test_golden_broad_question_deep_ablation_finds_no_load_bearing_source():
    sources = _load_sources()
    deep = quick_assess(sources, Q1, mode="deep")
    load_bearing = [p for p in deep["ablation"]["per_source"] if p["verdict"] == "load-bearing"]
    assert load_bearing == []  # every source is individually redundant on this broad a question
    assert len(deep["redundant_pairs"]) == 11
    assert len(deep["greedy_prune"]["kept_ids"]) == 1


def test_golden_specific_question_deep_ablation_finds_the_right_load_bearing_source():
    """A specific question that near-quotes one paper's own title: deep ablation
    correctly isolates that exact source as the sole load-bearing one, and
    greedy_prune's single survivor is that same source — the engine finding the
    right needle in a real, noisy, topically-adjacent haystack."""
    sources = _load_sources()
    deep = quick_assess(sources, Q2, mode="deep")
    load_bearing = [p["id"] for p in deep["ablation"]["per_source"] if p["verdict"] == "load-bearing"]
    assert load_bearing == ["00000000-0000-4000-8000-000000000000"]
    assert deep["greedy_prune"]["kept_ids"] == ["00000000-0000-4000-8000-000000000000"]


def test_golden_specific_question_fast_lane_misses_what_deep_catches():
    """Documents a real, live-data-discovered gap: the fast lane's "good" verdict
    (composite decent, coverage 1.0, source count under the length-scaled bloated
    threshold) does NOT imply low redundancy — deep mode here finds 15/16 sources
    redundant under the exact same question. escalate_to_deep is False, so a
    caller trusting the fast lane alone would miss this. Known limitation, tracked
    for a future non-length-based fast-lane redundancy proxy."""
    sources = _load_sources()
    fast = quick_assess(sources, Q2, mode="fast")
    assert fast["verdict"] == "good"
    assert fast["escalate_to_deep"] is False

    deep = quick_assess(sources, Q2, mode="deep")
    redundant = [p for p in deep["ablation"]["per_source"] if p["verdict"] == "redundant"]
    assert len(redundant) == 15
