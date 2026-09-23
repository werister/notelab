"""Multi-topic + multi-family sparring coverage — offline, deterministic.

Extends test_sparring.py (which covers one topic + the strong/weak/absent/boom
cases) with:
  * several DISTINCT research topics, each with topic-appropriate strong vs weak
    sources, asserting the quality scorer + ranking behave consistently per domain;
  * a 3-family race (not just head-to-head);
  * tie / near-tie handling;
  * an all-unavailable session (no winner, no crash).

All runners are injected fakes, so this needs no network, no API keys, no external
CLIs — it proves the sparring mechanics are sound before any live run.
"""
from notelab.research.quality import SourceFeatures
from notelab.research.sparring import run_sparring
from notelab.storage import Store


# --- topic fixtures: (question, strong_sources, weak_sources) --------------------

def _src(id_, url, word, n=200):
    return SourceFeatures(id=id_, url=url, content=(word + " ") * n)


TOPICS = {
    "quantum": (
        "quantum error correction thresholds",
        [_src("q1", "https://arxiv.org/abs/2401.001", "surface-code"),
         _src("q2", "https://www.nature.com/articles/x", "threshold")],
        [_src("q9", "https://top10-quantum-deals.com/buy", "buy", n=1)],
    ),
    "medicine": (
        "GLP-1 agonists and cardiovascular outcomes",
        [_src("m1", "https://www.nejm.org/doi/full/y", "semaglutide"),
         _src("m2", "https://pubmed.ncbi.nlm.nih.gov/123", "outcome")],
        [_src("m9", "https://miracle-weightloss-blog.net/ad", "miracle", n=1)],
    ),
    "climate": (
        "atmospheric methane sinks and hydroxyl radical",
        [_src("c1", "https://www.ipcc.ch/report/z", "hydroxyl"),
         _src("c2", "https://agupubs.onlinelibrary.wiley.com/w", "methane")],
        [_src("c9", "https://climate-hoax-forum.example/rant", "hoax", n=1)],
    ),
    "software": (
        "lock-free concurrent hash map design",
        [_src("s1", "https://dl.acm.org/doi/10.1145/abc", "lock-free"),
         _src("s2", "https://www.usenix.org/conference/paper", "concurrent")],
        [_src("s9", "https://random-coding-tips.biz/x", "tip", n=1)],
    ),
}


def _topic_runner(topic_key):
    q, strong, weak = TOPICS[topic_key]

    def runner(family, prompt):
        if family == "strong":
            return strong, {"available": True}
        if family == "weak":
            return weak, {"available": True}
        if family == "mid":
            return strong[:1] + weak, {"available": True}
        if family == "absent":
            return [], {"available": False, "error": "not installed"}
        return [], {"available": True}
    return q, runner


# --- tests -----------------------------------------------------------------------

def test_strong_beats_weak_across_all_topics():
    """The scorer must prefer authoritative sources in EVERY domain, not just one."""
    for key in TOPICS:
        q, runner = _topic_runner(key)
        s = run_sparring(q, "find sources", ["strong", "weak"], runner=runner)
        assert s.winner is not None, key
        assert s.winner.family == "strong", f"{key}: expected strong to win"
        assert s.as_dict()["margin_over_runner_up"] > 0, key


def test_three_family_race_orders_strong_mid_weak():
    q, runner = _topic_runner("medicine")
    s = run_sparring(q, "p", ["weak", "strong", "mid"], runner=runner)
    order = [c["family"] for c in s.as_dict()["compare"]]
    assert order[0] == "strong"
    assert order[-1] == "weak"
    assert order == ["strong", "mid", "weak"]


def test_identical_families_tie_with_zero_margin():
    q, runner = _topic_runner("climate")
    # two families returning the SAME strong sources -> equal scores, ~0 margin
    def dup_runner(family, prompt):
        return runner("strong", prompt)
    s = run_sparring(q, "p", ["a", "b"], runner=dup_runner)
    assert s.winner is not None
    assert s.as_dict()["margin_over_runner_up"] == 0


def test_all_families_unavailable_no_winner_no_crash():
    q, runner = _topic_runner("software")
    def dead(family, prompt):
        return [], {"available": False, "error": "offline"}
    s = run_sparring(q, "p", ["absent", "absent2"], runner=dead)
    assert s.winner is None
    assert s.ranking == []
    d = s.as_dict()
    assert d["winner_family"] is None
    assert all(r["available"] is False for r in d["results"])


def test_cross_topic_leaderboard_accumulates(tmp_path):
    """Record one sparring session per topic; the leaderboard aggregates across all."""
    store = Store(tmp_path / "cross.db")
    for key in TOPICS:
        q, runner = _topic_runner(key)
        s = run_sparring(q, "p", ["strong", "weak"], runner=runner)
        store.record_sparring(s.as_dict())
    board = {b["family"]: b for b in store.family_leaderboard()}
    assert board["strong"]["sessions"] == len(TOPICS)
    assert board["weak"]["sessions"] == len(TOPICS)
    assert board["strong"]["mean_quality"] > board["weak"]["mean_quality"]
    store.close()
