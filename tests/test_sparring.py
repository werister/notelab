"""M4 multi-model-family sparring tests — offline, fake family runner."""
from notelab.research.quality import SourceFeatures
from notelab.research.sparring import run_sparring
from notelab.storage import Store


def _good():
    return [SourceFeatures(id="1", url="https://arxiv.org/a", content="quantum " * 200),
            SourceFeatures(id="2", url="https://nih.gov/b", content="error " * 200)]


def _weak():
    return [SourceFeatures(id="3", url="https://top10-deals.com/x", content="buy")]


def fake_runner(family, prompt):
    if family == "strong":
        return _good(), {"available": True}
    if family == "weak":
        return _weak(), {"available": True}
    if family == "absent":
        return [], {"available": False, "error": "not installed"}
    if family == "boom":
        raise RuntimeError("runner crashed")
    return [], {"available": True}


def test_sparring_ranks_families_by_source_quality():
    s = run_sparring("quantum error correction", "find sources",
                     ["strong", "weak"], runner=fake_runner)
    assert s.winner.family == "strong"
    d = s.as_dict()
    assert d["winner_family"] == "strong"
    assert d["margin_over_runner_up"] > 0
    assert [c["family"] for c in d["compare"]][0] == "strong"


def test_unavailable_family_recorded_not_ranked():
    s = run_sparring("q", "p", ["strong", "absent"], runner=fake_runner)
    absent = [r for r in s.results if r.family == "absent"][0]
    assert absent.available is False
    assert absent.report is None
    # absent family excluded from ranking
    assert all(r.family != "absent" for r in s.ranking)


def test_failing_family_does_not_sink_session():
    s = run_sparring("q", "p", ["strong", "boom"], runner=fake_runner)
    assert s.winner.family == "strong"
    boom = [r for r in s.results if r.family == "boom"][0]
    assert boom.error and "runner crashed" in boom.error


def test_sparring_ledger_and_leaderboard(tmp_path):
    store = Store(tmp_path / "s.db")
    s1 = run_sparring("q1", "p", ["strong", "weak"], runner=fake_runner)
    store.record_sparring(s1.as_dict())
    s2 = run_sparring("q2", "p", ["strong", "weak"], runner=fake_runner)
    store.record_sparring(s2.as_dict())
    board = store.family_leaderboard()
    fams = {b["family"]: b for b in board}
    assert fams["strong"]["mean_quality"] > fams["weak"]["mean_quality"]
    assert fams["strong"]["sessions"] == 2
    store.close()
