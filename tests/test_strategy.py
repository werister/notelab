"""M3 strategy-memory tests — offline, deterministic."""
import tempfile
from pathlib import Path

from notelab.research.strategy import (
    profile_from_history,
    suggest_strategies,
    tune_weights_from_winner,
)
from notelab.storage import Store


def test_suggest_bootstraps_with_no_history():
    arms = suggest_strategies("quantum computing", best_prompt=None)
    assert len(arms) == 1
    assert arms[0].name == "baseline"
    assert "quantum computing" in arms[0].prompt


def test_suggest_exploits_winner_plus_mutations():
    arms = suggest_strategies("q", best_prompt="find great sources", explore=2)
    assert arms[0].name == "winner"
    assert arms[0].prompt == "find great sources"
    assert len(arms) == 3  # winner + 2 mutations
    assert all("find great sources" in a.prompt for a in arms[1:])
    names = {a.name for a in arms}
    assert "explore_authority" in names and "explore_recency" in names


def test_explore_zero_returns_only_winner():
    arms = suggest_strategies("q", best_prompt="p", explore=0)
    assert len(arms) == 1 and arms[0].name == "winner"


def test_tune_weights_shifts_toward_strong_signals():
    # winner scored high on authority + recency, low elsewhere
    means = {"authority": 0.9, "recency": 0.8, "depth": 0.1, "relevance": 0.1, "diversity": 0.1}
    w = tune_weights_from_winner(means)
    base = tune_weights_from_winner({})  # neutral
    assert w.authority > base.authority
    assert w.recency > base.recency
    # still a valid normalized weight vector
    total = w.authority + w.diversity + w.recency + w.depth + w.relevance
    assert abs(total - 1.0) < 1e-6


def test_profile_from_history_carries_tuned_weights():
    means = {"authority": 0.9, "recency": 0.2, "depth": 0.2, "relevance": 0.2, "diversity": 0.1}
    p = profile_from_history("topic-x", "winning prompt", 0.77, means, runs=3)
    d = p.as_dict()
    assert d["topic"] == "topic-x"
    assert d["best_prompt"] == "winning prompt"
    assert d["runs"] == 3
    assert set(d["weights"]) == {"authority", "diversity", "recency", "depth", "relevance"}


def test_profile_ledger_roundtrip(tmp_path):
    store = Store(tmp_path / "s.db")
    p = profile_from_history("t", "p", 0.5, {"authority": 0.5, "recency": 0.5,
                                             "depth": 0.5, "relevance": 0.5, "diversity": 0.5},
                             runs=2)
    store.upsert_topic_profile(p.as_dict())
    got = store.get_topic_profile("t")
    assert got["topic"] == "t" and got["best_prompt"] == "p"
    assert got["runs"] == 2
    assert set(got["weights"]) == {"authority", "diversity", "recency", "depth", "relevance"}
    # upsert updates, not duplicates
    p2 = profile_from_history("t", "p2", 0.6, None, runs=3)
    store.upsert_topic_profile(p2.as_dict())
    assert store.get_topic_profile("t")["best_prompt"] == "p2"
    store.close()


def test_experiment_count_and_signal_means_from_ledger(tmp_path):
    store = Store(tmp_path / "s.db")
    result = {
        "question": "q1", "winner": "A", "winner_score": 0.8,
        "arms": [{"strategy": {"name": "A", "prompt": "pa"},
                  "report": {"quality_score": 0.8, "n_sources": 3,
                             "signal_means": {"authority": 0.9, "recency": 0.7,
                                              "depth": 0.5, "relevance": 0.6, "diversity": 0.4},
                             "weights": {}}}],
    }
    store.record_experiment(result, weights={})
    assert store.experiment_count("q1") == 1
    means = store.winner_signal_means("q1")
    assert means["authority"] == 0.9
    assert store.winner_signal_means("nope") is None
    store.close()
