"""Tests for self-evolving sources + skill trend evals — offline, deterministic."""
from datetime import datetime, timedelta, timezone

from notelab.research.evals import (
    Trend,
    detect_runners,
    eval_skill_trend,
    run_adversary,
)
from notelab.research.evolve import (
    Action,
    EvolutionPolicy,
    SourceStat,
    plan_evolution,
)
from notelab.research.quality import SourceFeatures

NOW = datetime.now(timezone.utc)


def _src(id, url="", content="", title="", days=30):
    return SourceFeatures(id=id, url=url, title=title, content=content,
                          published=NOW - timedelta(days=days))


# --- evolution ------------------------------------------------------------------

def test_create_novel_above_floor():
    plan, _ = plan_evolution(
        "nb", existing=[],
        candidates=[_src("c1", "https://arxiv.org/1", "quantum " * 200, "Quantum paper")],
        question="quantum")
    actions = {i.source_id: i.action for i in plan.items}
    assert actions["c1"] == Action.CREATE
    assert plan.creates == 1


def test_skip_weak_candidate():
    plan, _ = plan_evolution(
        "nb", existing=[],
        candidates=[_src("c1", "https://top10-cheap-deals.com/x", "buy", days=4000)],
        question="quantum error correction")
    a = [i for i in plan.items if i.source_id == "c1"][0]
    assert a.action == Action.SKIP


def test_merge_duplicate_candidate():
    existing = [_src("e1", "https://arxiv.org/1", "quantum error correction codes " * 50,
                     "Quantum error correction")]
    cand = [_src("c1", "https://arxiv.org/1", "quantum error correction codes " * 50,
                 "Quantum error correction")]
    plan, _ = plan_evolution("nb", existing=existing, candidates=cand, question="quantum")
    merge = [i for i in plan.items if i.action == Action.MERGE]
    assert merge and merge[0].source_id == "c1"
    assert merge[0].merges_with == "e1"


def test_evict_after_repeated_strikes():
    weak = _src("w1", "https://top10-cheap-deals.com/x", "buy", days=4000)
    pol = EvolutionPolicy(evict_after_strikes=2)
    stats = {}
    # cycle 1: one strike, not yet evicted
    plan, stats = plan_evolution("nb", existing=[weak], stats=stats, question="q", policy=pol)
    assert not any(i.action == Action.EVICT for i in plan.items)
    # cycle 2: second consecutive strike -> evict proposed
    plan, stats = plan_evolution("nb", existing=[weak], stats=stats, question="q", policy=pol)
    assert any(i.action == Action.EVICT and i.source_id == "w1" for i in plan.items)


def test_existing_same_domain_duplicate_is_merged():
    existing = [
        _src("e1", "https://arxiv.org/1", "quantum error correction codes " * 50,
             "Quantum error correction"),
        _src("e2", "https://arxiv.org/1", "quantum error correction codes " * 50,
             "Quantum error correction"),  # same domain + same text -> dup of e1
    ]
    plan, _ = plan_evolution("nb", existing=existing, question="quantum")
    merges = [i for i in plan.items if i.action == Action.MERGE]
    assert len(merges) == 1
    assert merges[0].merges_with == "e1"


def test_cross_domain_corroboration_is_preserved():
    # identical TEXT but DIFFERENT authoritative domains — must NOT be merged
    existing = [
        _src("e1", "https://nih.gov/a", "fault tolerance thresholds " * 50, "Thresholds"),
        _src("e2", "https://nature.com/b", "fault tolerance thresholds " * 50, "Thresholds"),
    ]
    plan, _ = plan_evolution("nb", existing=existing, question="fault tolerance")
    assert not any(i.action == Action.MERGE for i in plan.items)
    assert plan.kept == 2


def test_budget_evicts_lowest_ranked():
    existing = [_src(f"s{i}", f"https://site{i}.gov/x", "authoritative content " * 100)
                for i in range(5)]
    pol = EvolutionPolicy(keep_budget=3)
    plan, _ = plan_evolution("nb", existing=existing, question="content", policy=pol)
    assert plan.kept == 3
    assert plan.evicts == 2


def test_decay_lowers_score_when_not_refreshed():
    s = _src("s1", "https://ok.gov/x", "content " * 50)
    stats = {"s1": SourceStat(id="s1", score=0.6, observations=5, last_score=0.6)}
    plan, new_stats = plan_evolution("nb", existing=[s], stats=stats, question="content")
    # observations advanced and decay applied
    assert new_stats["s1"].observations == 6


# --- adversarial runners + evals ------------------------------------------------

def test_detect_runners_reports_availability():
    runners = detect_runners()
    names = {r.name for r in runners}
    assert {"opencode", "copilot", "qoder", "qwen"} <= names
    # each has an availability boolean; at least the structure is right
    assert all(isinstance(r.available, bool) for r in runners)


def test_missing_runner_is_skipped_not_raised():
    from notelab.research.evals import RunnerSpec
    spec = RunnerSpec("nope", "definitely-not-a-real-binary-xyz", ["run", "{prompt}"],
                      available=False, path=None)
    res = run_adversary(spec, "hello")
    assert res.available is False
    assert res.skipped_reason and "not on PATH" in res.skipped_reason


def test_template_without_prompt_placeholder_is_skipped_not_run():
    # A template lacking {prompt} would silently run the agent with no task and
    # report a meaningless success. run_adversary must refuse it as a recorded skip.
    from notelab.research.evals import RunnerSpec
    spec = RunnerSpec("bad", "sh", ["-c", "echo no-placeholder"],
                      available=True, path="/bin/sh")
    res = run_adversary(spec, "the real prompt")
    assert res.available is True
    assert res.returncode is None          # never executed
    assert res.skipped_reason and "{prompt}" in res.skipped_reason


def test_registry_ships_all_six_families():
    names = {r.name for r in detect_runners()}
    assert {"opencode", "copilot", "claude", "codex", "qoder", "qwen"} <= names


def test_trend_improving():
    e = eval_skill_trend("s", [0.30, 0.42, 0.55, 0.68])
    assert e.trend == Trend.IMPROVING
    assert e.slope > 0 and e.delta > 0


def test_trend_regressing():
    e = eval_skill_trend("s", [0.70, 0.55, 0.40, 0.28])
    assert e.trend == Trend.REGRESSING
    assert e.slope < 0


def test_trend_flat_within_band():
    e = eval_skill_trend("s", [0.50, 0.505, 0.498, 0.502])
    assert e.trend == Trend.FLAT


def test_trend_insufficient_data():
    assert eval_skill_trend("s", [0.5]).trend == Trend.INSUFFICIENT
    assert eval_skill_trend("s", []).trend == Trend.INSUFFICIENT
