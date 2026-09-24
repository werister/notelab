"""
End-to-end integration for the research_run_experiment path + edge cases.

Drives ToolLogic (the shared tool implementation) with a fake adapter — no network,
no nlmx — through the full loop: A/B run -> score -> ledger -> history -> suggest ->
learn_topic. This is the top-level 'does the whole thing hang together' test.
"""
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from notelab._tools import ToolLogic
from notelab.research.quality import SourceFeatures
from notelab.storage import Store

NOW = datetime.now(timezone.utc)


class FakeAdapter:
    """Deterministic stand-in for NlmxAdapter: the 'authoritative' prompt pulls
    strong sources, anything else pulls a weak content-farm set."""
    def research_run(self, strategy):
        if "authoritative" in strategy.prompt:
            srcs = [SourceFeatures(id="a1", url="https://arxiv.org/1", content="quantum " * 200,
                                   published=NOW - timedelta(days=20)),
                    SourceFeatures(id="a2", url="https://nih.gov/2", content="error " * 200,
                                   published=NOW - timedelta(days=40))]
        else:
            srcs = [SourceFeatures(id="b1", url="https://top10-deals.com/x", content="buy",
                                   published=NOW - timedelta(days=3000))]
        return srcs, {"notebook_id": f"nb-{strategy.name}", "task_id": f"t-{strategy.name}"}

    def list_sources(self, notebook_id):
        return []

    def source_content(self, sid):
        return ""


def _logic():
    tmp = tempfile.mkdtemp()
    return ToolLogic(adapter=FakeAdapter(), store=Store(Path(tmp) / "e2e.db"))


def test_run_experiment_end_to_end_then_history_and_suggest():
    logic = _logic()
    out = json.loads(logic.run_experiment(
        "quantum error correction",
        [{"name": "broad", "prompt": "tell me about quantum"},
         {"name": "authoritative", "prompt": "find authoritative primary sources"}]))
    assert out["winner"] == "authoritative"
    assert out["winner_score"] > out["ranking"][-1]["quality_score"]
    assert "experiment_id" in out

    # history reflects it
    hist = json.loads(logic.history())
    assert hist[0]["winner"] == "authoritative"

    # suggest_prompt reuses the winning prompt
    sug = json.loads(logic.suggest_prompt("quantum error correction"))
    assert sug["suggested_prompt"] == "find authoritative primary sources"
    assert sug["based_on_prior_winners"] >= 1

    # learn_topic derives a profile + next arms from that history
    learned = json.loads(logic.learn_topic("quantum error correction"))
    assert learned["profile"]["best_prompt"] == "find authoritative primary sources"
    assert learned["suggested_strategies"][0]["name"] == "winner"
    assert len(learned["suggested_strategies"]) >= 2  # winner + explore


def test_learn_topic_best_score_is_per_topic_not_global_latest():
    """Regression: learn_topic's best_score must come from the latest experiment FOR
    THIS QUESTION, not the latest experiment overall. Found live: research_learn_topic
    reported best_score=0.0 for a topic with a real, recorded winner, because a later
    unrelated experiment on a different question had since become the global-latest
    row. Reproduce that ordering here: run topic A, then a different topic B, then
    ask learn_topic about A -- its own score must still be found."""
    logic = _logic()
    out = json.loads(logic.run_experiment(
        "quantum error correction",
        [{"name": "authoritative", "prompt": "find authoritative primary sources"}]))
    assert out["winner_score"] > 0

    # an unrelated, later experiment on a different question
    logic.run_experiment(
        "a completely different topic",
        [{"name": "authoritative", "prompt": "find authoritative primary sources"}])

    learned = json.loads(logic.learn_topic("quantum error correction"))
    assert learned["profile"]["best_score"] == out["winner_score"]


def test_run_experiment_accepts_preset_weights():
    logic = _logic()
    out = json.loads(logic.run_experiment(
        "news topic",
        [{"name": "a", "prompt": "authoritative fresh sources"}],
        weights="news"))
    assert out["n_arms"] == 1
    assert out["arms"][0]["report"] is not None


def test_run_experiment_all_arms_fail_gracefully():
    class BoomAdapter:
        def research_run(self, strategy):
            raise RuntimeError("nlmx unreachable")
    logic = _logic()
    logic._adapter = BoomAdapter()
    out = json.loads(logic.run_experiment("q", [{"name": "x", "prompt": "p"}]))
    # a fully-failed experiment still returns a well-formed report, no winner
    assert out["winner"] is None
    assert out["arms"][0]["error"] and "nlmx unreachable" in out["arms"][0]["error"]


def test_history_missing_experiment():
    logic = _logic()
    out = json.loads(logic.history(experiment_id=999))
    assert "error" in out


def test_suggest_with_no_history_is_null():
    logic = _logic()
    out = json.loads(logic.suggest_prompt("never researched"))
    assert out["suggested_prompt"] is None
    assert out["based_on_prior_winners"] == 0
