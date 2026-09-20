"""A/B harness + ledger tests — offline, with a fake research runner."""
from datetime import datetime, timedelta, timezone

from notelab.research.ab import Strategy, run_ab, suggest_next_prompt
from notelab.research.quality import SourceFeatures
from notelab.storage import Store

NOW = datetime.now(timezone.utc)


def fake_runner_factory():
    """Returns a runner where the 'authoritative' strategy pulls better sources."""
    def runner(strategy: Strategy):
        if "authoritative" in strategy.prompt:
            srcs = [
                SourceFeatures(id="1", url="https://arxiv.org/a", content="quantum " * 300,
                               published=NOW - timedelta(days=20)),
                SourceFeatures(id="2", url="https://nih.gov/b", content="computing " * 300,
                               published=NOW - timedelta(days=40)),
                SourceFeatures(id="3", url="https://nature.com/c", content="error " * 300,
                               published=NOW - timedelta(days=60)),
            ]
        else:
            srcs = [
                SourceFeatures(id="4", url="https://top10-deals.com/x", content="buy",
                               published=NOW - timedelta(days=3000)),
                SourceFeatures(id="5", url="https://top10-deals.com/y", content="cheap",
                               published=NOW - timedelta(days=3000)),
            ]
        return srcs, {"notebook_id": f"nb-{strategy.name}", "task_id": f"t-{strategy.name}"}
    return runner


def test_ab_ranks_better_strategy_first():
    exp = run_ab(
        "quantum computing error correction",
        [
            Strategy(name="broad", prompt="tell me about quantum"),
            Strategy(name="authoritative", prompt="find authoritative primary quantum sources"),
        ],
        runner=fake_runner_factory(),
    )
    assert exp.winner is not None
    assert exp.winner.strategy.name == "authoritative"
    d = exp.as_dict()
    assert d["winner"] == "authoritative"
    assert d["margin_over_runner_up"] > 0
    assert [a["name"] for a in d["ranking"]][0] == "authoritative"


def test_failed_arm_does_not_sink_experiment():
    def runner(strategy: Strategy):
        if strategy.name == "boom":
            raise RuntimeError("nlmx exploded")
        return [SourceFeatures(id="1", url="https://arxiv.org/a", content="x" * 500)], {}
    exp = run_ab("q", [Strategy(name="ok", prompt="p"), Strategy(name="boom", prompt="p")], runner)
    assert exp.winner.strategy.name == "ok"
    boom = [a for a in exp.arms if a.strategy.name == "boom"][0]
    assert boom.error and "nlmx exploded" in boom.error


def test_ledger_roundtrip_and_prior_winners(tmp_path):
    store = Store(tmp_path / "t.db")
    exp = run_ab(
        "quantum computing error correction",
        [Strategy(name="broad", prompt="broad q"),
         Strategy(name="authoritative", prompt="authoritative primary q")],
        runner=fake_runner_factory(),
    )
    result = exp.as_dict()
    weights = result["arms"][0]["report"]["weights"]
    exp_id = store.record_experiment(result, weights)
    assert exp_id == 1

    listed = store.list_experiments()
    assert listed[0]["winner"] == "authoritative"

    fetched = store.get_experiment(exp_id)
    assert fetched["question"] == "quantum computing error correction"

    winners = store.prior_winners("quantum computing error correction")
    assert winners == ["authoritative primary q"]
    assert suggest_next_prompt(winners) == "authoritative primary q"
    store.close()


def test_suggest_none_when_no_history():
    assert suggest_next_prompt([]) is None
