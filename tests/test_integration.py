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


# ---------------------------------------------------------------------------
# semantic_expansions opt-in (research/semantic.py was tested but unreachable)
# ---------------------------------------------------------------------------

class _SourceAdapter(FakeAdapter):
    def list_sources(self, notebook_id):
        return [SourceFeatures(id="s1", url="https://arxiv.org/abs/1", title="surface codes",
                               content="mwpm decoder " * 50,
                               published=NOW - timedelta(days=10)),
                SourceFeatures(id="s2", url="https://blog.example/x", title="deals",
                               content="buy now cheap", published=NOW - timedelta(days=900))]


def _logic_with_sources():
    tmp = tempfile.mkdtemp()
    return ToolLogic(adapter=_SourceAdapter(), store=Store(Path(tmp) / "sem.db"))


def test_score_notebook_semantic_opt_in(monkeypatch):
    from notelab.research.semantic import SemanticExpansionResult
    seen = {}

    def fake_expansions(question, sources, **kw):
        seen["n_sources"] = len(sources)
        return SemanticExpansionResult({"decoder": {"mwpm"}}, ["decoder"], 10, 0.78)

    monkeypatch.setattr("notelab.research.semantic.semantic_term_expansions", fake_expansions)
    out = json.loads(_logic_with_sources().score_notebook(
        "nb1", question="decoder", semantic_expansions=True))
    assert out["semantic_expansions_applied"] is True
    assert out["semantic_expansion_count"] == 1
    assert seen["n_sources"] == 2  # expansions see the same backfilled sources


def test_score_notebook_semantic_default_off_never_calls_api(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("semantic path must not run by default")

    monkeypatch.setattr("notelab.research.semantic.semantic_term_expansions", boom)
    out = json.loads(_logic_with_sources().score_notebook("nb1", question="decoder"))
    assert "semantic_expansions_applied" not in out


def test_score_notebook_semantic_failure_is_json_error(monkeypatch):
    from notelab.research.semantic import SemanticExpansionError

    def fail(*a, **k):
        raise SemanticExpansionError("needs a Mistral API key")

    monkeypatch.setattr("notelab.research.semantic.semantic_term_expansions", fail)
    out = json.loads(_logic_with_sources().score_notebook(
        "nb1", question="decoder", semantic_expansions=True))
    assert "Mistral API key" in out["error"]
    assert "hint" in out


def test_assess_notebook_semantic_opt_in(monkeypatch):
    from notelab.research.semantic import SemanticExpansionResult

    def fake_expansions(question, sources, **kw):
        return SemanticExpansionResult({"decoder": {"mwpm", "matching"}},
                                       ["decoder"], 10, 0.78)

    monkeypatch.setattr("notelab.research.semantic.semantic_term_expansions", fake_expansions)
    out = json.loads(_logic_with_sources().assess_notebook(
        "nb1", question="decoder", mode="fast", semantic_expansions=True))
    assert out["semantic_expansions_applied"] is True
    assert out["semantic_expansion_count"] == 2


def test_assess_notebook_semantic_failure_is_json_error(monkeypatch):
    from notelab.research.semantic import SemanticExpansionError

    def fail(*a, **k):
        raise SemanticExpansionError("needs a Mistral API key")

    monkeypatch.setattr("notelab.research.semantic.semantic_term_expansions", fail)
    out = json.loads(_logic_with_sources().assess_notebook(
        "nb1", question="decoder", semantic_expansions=True))
    assert "Mistral API key" in out["error"]
    assert "hint" in out


# ---------------------------------------------------------------------------
# tool-level coverage: weights presets, backfill, evolve, eval, catalog,
# leaderboard, spar-timeout (all previously untested through ToolLogic)
# ---------------------------------------------------------------------------

def test_weights_from_preset_dict_and_unknown():
    from notelab._tools import _weights_from
    from notelab.research.quality import QualityWeights
    assert _weights_from("news").recency == 0.35
    assert _weights_from("no-such-preset") == QualityWeights()
    assert _weights_from({"relevance": 0.9}).relevance == 0.9
    assert _weights_from({"bogus": 1.0}) == QualityWeights()


def test_lazy_default_store_constructed_once_on_access(monkeypatch, tmp_path):
    made = []

    def spy_store():
        made.append(True)
        return Store(tmp_path / "lazy.db")

    monkeypatch.setattr("notelab._tools.Store", spy_store)
    logic = ToolLogic()  # no store, no adapter
    assert made == []  # constructing logic touches no DB
    assert isinstance(logic.store, Store)
    assert logic.store is logic.store  # cached, constructed once
    assert len(made) == 1


def test_score_notebook_backfills_missing_content(tmp_path):
    class BareAdapter(FakeAdapter):
        def __init__(self):
            self.fetched = []

        def list_sources(self, notebook_id):
            return [SourceFeatures(id="s1", url="https://arxiv.org/x",
                                   content="",
                                   published=NOW - timedelta(days=5))]

        def source_content(self, sid):
            self.fetched.append(sid)
            return "quantum content " * 20

    adapter = BareAdapter()
    logic = ToolLogic(adapter=adapter, store=Store(Path(tmp_path) / "b.db"))
    out = json.loads(logic.score_notebook("nb1", question="quantum"))
    assert adapter.fetched == ["s1"]
    assert out["n_sources"] == 1
    assert out["quality_score"] > 0


def test_evolve_notebook_dry_run_removes_nothing(tmp_path):
    class EvolveAdapter(FakeAdapter):
        def __init__(self):
            self.removed = []

        def list_sources(self, notebook_id):
            return [SourceFeatures(id="s1", url="https://arxiv.org/1",
                                   content="quantum " * 100,
                                   published=NOW - timedelta(days=5)),
                    SourceFeatures(id="s2", url="https://spam.test/x",
                                   content="",  # contentless: exercises backfill
                                   published=NOW - timedelta(days=900))]

        def source_content(self, sid):
            return "recovered content"

        def remove_source(self, notebook_id, source_id):
            self.removed.append(source_id)
            return True, "removed"

    adapter = EvolveAdapter()
    logic = ToolLogic(adapter=adapter, store=Store(Path(tmp_path) / "e.db"))
    dry = json.loads(logic.evolve_notebook("nb1", question="quantum"))
    assert dry["applied"] is False
    assert "dry run" in dry["note"]
    assert adapter.removed == []  # the safety property
    assert dry["execution"]["confirmed"] is False

    applied = json.loads(logic.evolve_notebook("nb1", question="quantum", apply=True))
    assert applied["applied"] is True
    assert applied["execution"]["confirmed"] is True


def test_eval_skill_with_no_history_is_insufficient():
    out = json.loads(_logic().eval_skill("never-seen"))
    assert out["trend"] == "insufficient_data"
    assert out["scores"] == []


def test_catalog_sync_then_search(tmp_path):
    class CatAdapter(FakeAdapter):
        def list_notebooks(self):
            return [{"id": "nb1", "title": "T"}]

        def list_sources(self, notebook_id):
            return [SourceFeatures(id="s1", title="quantum stuff",
                                   url="https://x.test/1",
                                   content="quantum " * 30,
                                   published=NOW - timedelta(days=5))]

    logic = ToolLogic(adapter=CatAdapter(), store=Store(Path(tmp_path) / "c.db"))
    rep = json.loads(logic.catalog_sync())
    assert rep["sources_synced"] == 1 and rep["errors"] == []
    hit = json.loads(logic.catalog_search("quantum"))
    assert hit["query"] == "quantum" and len(hit["results"]) == 1
    miss = json.loads(logic.catalog_search("zzz-no-match-here"))
    assert miss["results"] == []


def test_family_leaderboard_empty():
    out = json.loads(_logic().family_leaderboard())
    assert out == {"leaderboard": []}


def test_spar_family_timeout_recorded(monkeypatch):
    from notelab.research.evals import RunnerResult, RunnerSpec
    specs = [RunnerSpec("opencode", "opencode", ["run", "{prompt}"],
                        available=True, path="/o")]
    monkeypatch.setattr("notelab._tools.detect_runners_with_reliability",
                        lambda: specs)

    def fake_run_adversary(spec, prompt, timeout=180, cwd=None):
        return RunnerResult(spec.name, True, timed_out=True)

    monkeypatch.setattr("notelab._tools.run_adversary", fake_run_adversary)
    out = json.loads(_logic().spar("q", "p", families=["opencode"]))
    entry = next(r for r in out["results"] if r["family"] == "opencode")
    assert entry["error"] == "timed out"
