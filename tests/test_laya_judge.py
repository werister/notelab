"""laya_judge — the Laya-based ablation tie-breaker (research/laya_judge.py).

Offline tests mock the loaded agent's .predict() call (no laya-mlx import needed,
runs in CI). A separate live test only runs when laya_available() is True (mirrors
test_semantic_expansions.py's MISTRAL_API_KEY gate) and makes a real model call.

Uses a `score` question (specificity, 0-3), not a direct `choice` between
"redundant"/"load_bearing" -- verified live that the direct binary choice produced
a near-coin-flip (~52/48, confidence <0.02) regardless of how differentiated the
two sources actually were, while scoring each source's own specificity
independently gave a clean, confident signal instead.
"""
from unittest.mock import patch

import pytest

from notelab.research.laya_judge import LayaJudgeError, judge_redundancy, laya_available
from notelab.research.quality import SourceFeatures


def _s(i, dom, text):
    return SourceFeatures(id=i, url=f"https://{dom}/{i}", content=text)


def _fake_agent(score: float, confidence: float = 0.5):
    fake = type("FakeAgent", (), {})()
    fake.predict = lambda state, questions: {
        "answers": {
            "specificity": {
                "type": "score", "confidence": confidence, "score": score,
                "probabilities": {"0": 0.1, "1": 0.2, "2": 0.4, "3": 0.3},
            }
        }
    }
    return fake


def test_laya_available_false_when_import_fails():
    with patch.dict("sys.modules", {"laya_mlx": None}):
        assert laya_available() is False


def test_judge_redundancy_high_score_is_load_bearing_hyphenated():
    """Verdict strings must be this repo's existing hyphenated convention
    ("load-bearing", not "load_bearing") -- ablate_sources' AblationReport
    .cut_candidates / .keep_locked filter on the exact hyphenated string."""
    with patch("notelab.research.laya_judge._get_agent", return_value=_fake_agent(2.5)):
        judgment = judge_redundancy(
            _s("A", "arxiv.org", "real content"), [], "some question")
    assert judgment.verdict == "load-bearing"
    assert judgment.confidence == 0.5


def test_judge_redundancy_low_score_is_redundant():
    with patch("notelab.research.laya_judge._get_agent", return_value=_fake_agent(0.6)):
        judgment = judge_redundancy(
            _s("B", "example.com", "content"), [], "question")
    assert judgment.verdict == "redundant"


def test_judge_redundancy_threshold_is_inclusive_at_midpoint():
    with patch("notelab.research.laya_judge._get_agent", return_value=_fake_agent(1.5)):
        judgment = judge_redundancy(_s("C", "x.com", "c"), [], "q")
    assert judgment.verdict == "load-bearing"


def test_judge_redundancy_raises_when_laya_not_available():
    # _agent is a module-level singleton (research/laya_judge.py) that persists
    # across tests within one process -- if another test (e.g. the live test)
    # already populated it, _get_agent() would return the cached agent without
    # re-checking laya_available() at all, and this test would pass for the
    # wrong reason (or not exercise the check it claims to). Force a fresh state.
    with patch("notelab.research.laya_judge._agent", None), \
         patch("notelab.research.laya_judge.laya_available", return_value=False):
        with pytest.raises(LayaJudgeError, match="laya-mlx"):
            judge_redundancy(_s("A", "x.com", "c"), [], "q")


def test_laya_available_true_when_importable(monkeypatch):
    import sys
    import types
    monkeypatch.setitem(sys.modules, "laya_mlx", types.ModuleType("laya_mlx"))
    assert laya_available() is True


def test_get_agent_caches_successful_load(monkeypatch):
    import sys
    import types

    import notelab.research.laya_judge as lj

    fake = types.ModuleType("laya_mlx")
    calls = []
    sentinel = object()
    fake.load = lambda *a, **k: calls.append(1) or sentinel
    monkeypatch.setitem(sys.modules, "laya_mlx", fake)
    monkeypatch.setattr(lj, "_agent", None)
    assert lj._get_agent() is sentinel
    assert lj._get_agent() is sentinel
    assert len(calls) == 1  # the ~36s load happens once per process


def test_get_agent_load_failure_raises_not_caches(monkeypatch):
    import sys
    import types

    import notelab.research.laya_judge as lj

    fake = types.ModuleType("laya_mlx")

    def boom(*a, **k):
        raise RuntimeError("hub down")

    fake.load = boom
    monkeypatch.setitem(sys.modules, "laya_mlx", fake)
    monkeypatch.setattr(lj, "_agent", None)
    with pytest.raises(LayaJudgeError, match="failed to load"):
        lj._get_agent()
    assert lj._agent is None  # failure must not poison the singleton


def test_judge_redundancy_predict_failure_raises():
    fake = type("FakeAgent", (), {})()

    def boom(state, questions):
        raise RuntimeError("nan logits")

    fake.predict = boom
    with patch("notelab.research.laya_judge._get_agent", return_value=fake):
        with pytest.raises(LayaJudgeError, match="prediction failed"):
            judge_redundancy(_s("A", "x.com", "content here"), [], "q")
