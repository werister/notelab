"""quick_assess wired into the live research return path.

research_run_experiment (research/ab.py:run_ab) now runs quick_assess(mode="fast")
on every arm's sources as they come back from the runner, and research_assess
exposes the same engine on demand (fast or deep) for an EXISTING notebook. Both are
offline/deterministic — a fake runner/adapter stands in for nlmx.
"""
from notelab._tools import ToolLogic
from notelab.research.ab import Strategy, run_ab
from notelab.research.quality import SourceFeatures


def _s(i, dom, text, n=120):
    return SourceFeatures(id=i, url=f"https://{dom}/{i}", content=(text + " ") * n)


Q = "quantum error correction decoder"


def test_run_ab_attaches_good_assessment_to_arm():
    def runner(strategy: Strategy):
        return [_s("A", "arxiv.org", "quantum error correction decoder")], {"notebook_id": "nb1"}

    exp = run_ab(Q, [Strategy(name="s", prompt="p")], runner=runner)
    arm = exp.arms[0]
    assert arm.assessment is not None
    assert arm.assessment["mode"] == "fast"
    assert arm.assessment["verdict"] == "good"
    d = exp.as_dict()
    assert d["arms"][0]["assessment"]["verdict"] == "good"
    assert d["winner_assessment"]["verdict"] == "good"  # winner's verdict, explicitly named


def test_run_ab_attaches_thin_assessment_on_missing_coverage():
    def runner(strategy: Strategy):
        return [_s("A", "arxiv.org", "quantum error correction")], {"notebook_id": "nb1"}  # no 'decoder'

    exp = run_ab(Q, [Strategy(name="s", prompt="p")], runner=runner)
    a = exp.arms[0].assessment
    assert a["verdict"] == "thin"
    assert "decoder" in a["coverage_gaps"]
    assert a["escalate_to_deep"] is True


def test_run_ab_attaches_bloated_assessment_on_redundant_pile():
    many = [_s(str(i), "arxiv.org", "quantum error correction decoder") for i in range(12)]

    def runner(strategy: Strategy):
        return many, {"notebook_id": "nb1"}

    exp = run_ab(Q, [Strategy(name="s", prompt="p")], runner=runner)
    assert exp.arms[0].assessment["verdict"] == "bloated"


def test_run_ab_skips_assessment_when_runner_raises():
    def runner(strategy: Strategy):
        raise RuntimeError("nlmx exploded")

    exp = run_ab(Q, [Strategy(name="boom", prompt="p")], runner=runner)
    assert exp.arms[0].error is not None
    assert exp.arms[0].assessment is None


def test_run_ab_no_winner_means_no_top_level_assessment():
    def runner(strategy: Strategy):
        raise RuntimeError("down")

    exp = run_ab(Q, [Strategy(name="boom", prompt="p")], runner=runner)
    assert exp.as_dict()["winner_assessment"] is None


def test_run_ab_still_assesses_on_soft_error_from_runner():
    """A runner that returns (sources, meta) with meta["error"] set — NOT an
    exception — still gets a real assessment: an empty/partial source list is
    itself informative ("thin"), and `report` is already computed unconditionally
    in this case, so `assessment` should match that behavior (sparring review)."""
    def runner(strategy: Strategy):
        return [], {"notebook_id": "nb1", "error": "nlmx: quota exceeded"}

    exp = run_ab(Q, [Strategy(name="s", prompt="p")], runner=runner)
    arm = exp.arms[0]
    assert arm.error == "nlmx: quota exceeded"
    assert arm.assessment is not None
    assert arm.assessment["verdict"] == "thin"


def test_run_ab_isolates_assessment_exception_from_arm_success(monkeypatch):
    """A bug inside quick_assess must not masquerade as the RUNNER having failed —
    it should not discard an otherwise-good arm's sources/report (sparring review:
    both were sharing one try/except, so this used to poison the whole arm)."""
    import notelab.research.ab as ab_mod

    def boom(*a, **k):
        raise RuntimeError("assessment engine bug")
    monkeypatch.setattr(ab_mod, "quick_assess", boom)

    def runner(strategy: Strategy):
        return [_s("A", "arxiv.org", "quantum error correction decoder")], {"notebook_id": "nb1"}

    exp = run_ab(Q, [Strategy(name="s", prompt="p")], runner=runner)
    arm = exp.arms[0]
    assert arm.error is None  # the arm itself succeeded
    assert len(arm.sources) == 1
    assert arm.assessment == {"error": "RuntimeError: assessment engine bug"}


class _StubAdapter:
    def __init__(self, sources):
        self._sources = sources

    def list_sources(self, notebook_id):
        return self._sources

    def source_content(self, sid):
        return ""


def test_research_assess_tool_fast_mode():
    sources = [_s("A", "arxiv.org", "quantum error correction decoder")]
    logic = ToolLogic(adapter=_StubAdapter(sources))
    import json
    payload = json.loads(logic.assess_notebook("nb1", question=Q))
    assert payload["mode"] == "fast"
    assert payload["verdict"] == "good"


def test_research_assess_tool_deep_mode():
    sources = [_s("A", "arxiv.org", "quantum error correction"),
               _s("B", "blog.example", "quantum error correction"),
               _s("C", "nature.com", "decoder")]
    logic = ToolLogic(adapter=_StubAdapter(sources))
    import json
    payload = json.loads(logic.assess_notebook("nb1", question=Q, mode="deep"))
    assert payload["mode"] == "deep"
    assert "ablation" in payload and "redundant_pairs" in payload and "greedy_prune" in payload


def test_research_assess_rejects_invalid_mode():
    logic = ToolLogic(adapter=_StubAdapter([]))
    import pytest
    with pytest.raises(ValueError):
        logic.assess_notebook("nb1", question=Q, mode="Deeep")


def test_research_assess_mode_is_case_insensitive():
    sources = [_s("A", "arxiv.org", "quantum error correction decoder")]
    logic = ToolLogic(adapter=_StubAdapter(sources))
    import json
    payload = json.loads(logic.assess_notebook("nb1", question=Q, mode="FAST"))
    assert payload["mode"] == "fast"


def test_research_assess_empty_question_is_unassessable_not_good():
    """An empty question can't honestly be scored 'good' off composite/count alone
    (sparring review: this used to silently report 'good' with coverage 0.0)."""
    sources = [_s(str(i), "arxiv.org", "quantum error correction decoder") for i in range(2)]
    logic = ToolLogic(adapter=_StubAdapter(sources))
    import json
    payload = json.loads(logic.assess_notebook("nb1", question=""))
    assert payload["verdict"] == "unassessable"


class _CountingAdapter:
    """Tracks which source ids got a source_content() call, to prove deep mode
    backfills ALL sources rather than silently capping at the fast lane's 15."""
    def __init__(self, sources):
        self._sources = sources
        self.content_calls: list[str] = []

    def list_sources(self, notebook_id):
        return self._sources

    def source_content(self, sid):
        self.content_calls.append(sid)
        return "quantum error correction decoder " * 50


def test_research_assess_deep_mode_backfills_all_sources_beyond_fifteen():
    sources = [SourceFeatures(id=str(i), url=f"https://arxiv.org/{i}", content="")
               for i in range(20)]  # no content yet
    adapter = _CountingAdapter(sources)
    logic = ToolLogic(adapter=adapter)
    logic.assess_notebook("nb1", question=Q, mode="deep")
    assert len(adapter.content_calls) == 20  # not capped at 15


def test_research_assess_fast_mode_still_caps_at_fifteen():
    sources = [SourceFeatures(id=str(i), url=f"https://arxiv.org/{i}", content="")
               for i in range(20)]
    adapter = _CountingAdapter(sources)
    logic = ToolLogic(adapter=adapter)
    logic.assess_notebook("nb1", question=Q, mode="fast")
    assert len(adapter.content_calls) == 15  # unchanged cheap-lane behavior
