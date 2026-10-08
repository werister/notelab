"""Tests for self-evolving sources + skill trend evals — offline, deterministic."""
from datetime import datetime, timedelta, timezone
import time

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


def test_registry_ships_all_ten_families():
    names = {r.name for r in detect_runners()}
    assert {"opencode", "copilot", "claude", "codex", "qoder", "qwen",
            "muse", "cline", "kilo", "kiro-cli"} <= names


def test_registry_includes_a_local_ollama_runner():
    # A local model (no API key, no per-token cost, no rate limit) belongs in the
    # armada alongside the hosted CLIs -- ollama's stdout is clean when captured via
    # subprocess with separate stdout/stderr pipes (confirmed live: the spinner
    # animation goes to stderr only), so it needs no special wrapping to be a normal
    # RunnerSpec like every other entry.
    names = {r.name for r in detect_runners()}
    assert "ollama-llama3.2" in names


def test_template_with_cwd_placeholder_and_no_cwd_given_is_skipped():
    from notelab.research.evals import RunnerSpec
    spec = RunnerSpec("needs-cwd", "sh", ["-c", "{prompt}", "{cwd}"],
                      available=True, path="/bin/sh")
    res = run_adversary(spec, "echo hi")  # no cwd= passed
    assert res.available is True
    assert res.returncode is None          # never executed
    assert res.skipped_reason and "{cwd}" in res.skipped_reason


def test_cwd_placeholder_is_substituted_when_cwd_given(tmp_path):
    from notelab.research.evals import RunnerSpec
    marker = tmp_path / "cwd-seen.txt"
    spec = RunnerSpec("echo-cwd", "sh",
                      ["-c", f"pwd > {marker}; echo {{prompt}}"],
                      available=True, path="/bin/sh")
    res = run_adversary(spec, "hello", cwd=str(tmp_path))
    assert res.returncode == 0
    assert marker.read_text().strip() == str(tmp_path.resolve())


def test_timeout_kills_process_group_including_grandchildren(tmp_path):
    from notelab.research.evals import RunnerSpec
    # The child backgrounds a grandchild that touches `marker` ~2s after the parent's
    # own 1s timeout fires. The check below MUST happen after that T+2s deadline, not
    # before it: if it ran too early (as an earlier version of this test did, with a
    # T+5s deadline checked at T+3s), the marker would legitimately not exist yet
    # regardless of whether the process-group kill worked at all -- a tautological
    # test that passes even with a real regression to killing only the direct child.
    marker = tmp_path / "grandchild_alive"
    script = tmp_path / "spawn.sh"
    script.write_text(
        "#!/bin/sh\n"
        f"(sleep 2; touch {marker}) &\n"
        "sleep 5\n"
    )
    script.chmod(0o755)
    spec = RunnerSpec("spawner", str(script), ["{prompt}"], available=True, path=str(script))

    res = run_adversary(spec, "unused", timeout=1)
    assert res.timed_out is True

    time.sleep(2.5)  # T+3.5s total; past the grandchild's T+2s deadline, with margin
    assert not marker.exists(), "grandchild survived the timeout kill"


def test_research_runners_never_carry_write_trust_flags():
    # Component A only ever asks a research question -- it must never be handed a
    # flag that grants an agent filesystem write trust.
    dangerous = {"--yolo", "--auto", "-a", "--trust-all-tools"}
    for r in detect_runners():
        assert not (dangerous & set(r.args_template)), \
            f"{r.name} carries a write-trust flag in the research-only runner table"


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


def test_runner_spec_has_a_provider_field_defaulting_empty():
    from notelab.research.evals import RunnerSpec
    spec = RunnerSpec("opencode", "opencode", ["run", "{prompt}"])
    assert spec.provider == ""  # non-breaking default for every existing single-model entry


def test_known_runners_names_are_git_branch_safe():
    # Every RunnerSpec.name must survive git's OWN branch-ref validation for
    # swarm/<name>/<slug> -- delegates to `git check-ref-format --branch` directly
    # rather than a hand-rolled regex: a prior version of this test used
    # [a-zA-Z0-9_.-]+, which is under-inclusive (accepts a leading/trailing ".",
    # "..", and a leading "-", all of which git itself rejects or mishandles) --
    # exactly the gap this test exists to catch before real composite names land.
    import subprocess
    for r in detect_runners():
        candidate = f"swarm/{r.name}/some-task-slug"
        result = subprocess.run(["git", "check-ref-format", "--branch", candidate],
                                capture_output=True, text=True)
        assert result.returncode == 0, \
            f"{r.name!r} is not git-branch-safe: {result.stderr.strip()}"


def test_ols_slope_needs_two_points():
    from notelab.research.evals import _ols_slope
    assert _ols_slope([]) == 0.0
    assert _ols_slope([0.5]) == 0.0
    assert _ols_slope([0.0, 1.0]) == 1.0


def test_detect_runners_with_reliability_uses_default_ledger(monkeypatch):
    import notelab.research.evals as E
    from notelab.research import reliability as R
    seen = {}

    def fake_filter(specs, ledger):
        seen["ledger"] = ledger
        return specs

    monkeypatch.setattr(R, "filter_known_unreliable", fake_filter)
    out = E.detect_runners_with_reliability()
    assert seen["ledger"] == R.DEFAULT_MML_LEDGER
    assert {"opencode", "claude"} <= {r.name for r in out}


def test_cwd_placeholder_substituted_into_argv(tmp_path):
    from notelab.research.evals import RunnerSpec
    spec = RunnerSpec("show-cwd", "sh", ["-c", "echo Template saw: {cwd} {prompt}"],
                      available=True, path="/bin/sh")
    res = run_adversary(spec, "hello", cwd=str(tmp_path))
    assert res.returncode == 0
    assert f"Template saw: {tmp_path} hello" in res.stdout


def test_timeout_kill_races_resolve_to_timed_out(monkeypatch):
    """The timeout path's two race branches (process group already gone;
    escaped grandchild holding the pipe past the bounded reap) must both
    resolve to timed_out, never hang or raise. Faked Popen: triggering these
    races with real processes would be timing-flaky by construction."""
    import subprocess as sp_mod

    import notelab.research.evals as E
    from notelab.research.evals import RunnerSpec

    class RacingProc:
        pid = 424242
        returncode = None

        def communicate(self, timeout=None):
            raise sp_mod.TimeoutExpired(cmd="x", timeout=timeout)

    monkeypatch.setattr(E.subprocess, "Popen", lambda *a, **k: RacingProc())
    monkeypatch.setattr(E.os, "getpgid", lambda pid: 1)

    def gone(pgid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(E.os, "killpg", gone)
    spec = RunnerSpec("x", "x", ["{prompt}"], available=True, path="/x")
    res = run_adversary(spec, "hi", timeout=1)
    assert res.timed_out is True
    assert res.returncode is None


def test_plan_item_merge_serializes_target():
    from notelab.research.evolve import PlanItem
    d = PlanItem(Action.MERGE, "a", 0.9, "dupe", merges_with="b").as_dict()
    assert d["merges_with"] == "b"
    assert "merges_with" not in PlanItem(Action.KEEP, "a", 0.9, "fine").as_dict()


def test_similarity_falls_back_to_domain_without_text():
    from notelab.research.evolve import _similarity
    a = SourceFeatures(id="a", url="https://x.test/1", title="", content="")
    b = SourceFeatures(id="b", url="https://x.test/2", title="", content="")
    c = SourceFeatures(id="c", url="https://other.test/1", title="", content="")
    assert _similarity(a, b) == 1.0
    assert _similarity(a, c) == 0.0


def test_over_budget_skips_created_but_keeps_sole_cover():
    # keep_budget=1 over {strong, sole-cover, novel candidate}: the candidate
    # SKIPs (never create-then-evict) while the sole cover of "decoder" KEEPs
    # despite being over budget.
    strong = SourceFeatures(id="strong", url="https://arxiv.org/1",
                            title="quantum deep dive", content="quantum " * 200,
                            published=NOW - timedelta(days=5))
    sole = SourceFeatures(id="sole", url="https://blog.test/d",
                          title="decoder notes", content="decoder blueprint",
                          published=NOW - timedelta(days=5))
    new = SourceFeatures(id="new", url="https://x.test/n",
                         title="quantum research", content="quantum research",
                         published=NOW - timedelta(days=500))
    pol = EvolutionPolicy(keep_budget=1, quality_floor=0.0)
    plan, _ = plan_evolution("nb1", existing=[strong, sole], candidates=[new],
                             question="quantum decoder", policy=pol)
    by_id = {i.source_id: i for i in plan.items}
    assert by_id["strong"].action == Action.KEEP
    assert by_id["new"].action == Action.SKIP
    assert "over keep_budget" in by_id["new"].reason
    assert by_id["sole"].action == Action.KEEP
    assert "coverage guard keeps it" in by_id["sole"].reason
