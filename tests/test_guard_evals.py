"""Offline tests for the guard-eval harness itself — no live agent CLIs here.

See research/guard_evals.py's __main__ for the live sweep (run manually, not
in CI: it costs real API calls on whichever model-family CLIs are installed).
"""
from notelab.research.evals import RunnerResult, RunnerSpec
from notelab.research.guard_evals import (
    SCENARIOS,
    Scenario,
    run_eval,
    score_transcript,
)


def _scenario(**kw):
    base = {"name": "s", "guard": "g", "prompt_template": "do {notebook_id}",
            "refusal_markers": ("broken upstream",)}
    base.update(kw)
    return Scenario(**base)


def test_score_transcript_detects_refusal_marker():
    result = RunnerResult("a", True, returncode=1,
                          stdout="nlmx: --source-ids is confirmed broken upstream")
    scored = score_transcript(_scenario(), result)
    assert scored["guard_respected"] is True
    assert "broken upstream" in scored["markers_seen"]


def test_score_transcript_flags_missed_refusal():
    result = RunnerResult("a", True, returncode=0, stdout="sources_used: []")
    scored = score_transcript(_scenario(), result)
    assert scored["guard_respected"] is False
    assert scored["markers_seen"] == []


def test_score_transcript_matching_is_case_insensitive():
    result = RunnerResult("a", True, returncode=1, stdout="BROKEN UPSTREAM bug")
    assert score_transcript(_scenario(), result)["guard_respected"] is True


def test_score_transcript_passes_through_non_runs():
    scenario = _scenario()
    assert score_transcript(scenario, RunnerResult("a", False))["guard_respected"] is None
    assert score_transcript(
        scenario, RunnerResult("a", True, timed_out=True))["guard_respected"] is None
    assert score_transcript(
        scenario, RunnerResult("a", True, skipped_reason="nope"))["guard_respected"] is None


def test_run_eval_skips_source_scenario_without_source_id(monkeypatch):
    calls = []

    def fake_run_adversary(spec, prompt, timeout=90):
        calls.append((spec.name, prompt))
        return RunnerResult(spec.name, True, returncode=1, stdout="refused")

    monkeypatch.setattr("notelab.research.guard_evals.run_adversary", fake_run_adversary)
    monkeypatch.setattr("notelab.research.guard_evals.detect_runners", lambda c=None: c or [])
    spec = RunnerSpec("fake", "fake", ["{prompt}"], available=True, path="/fake")
    report = run_eval("nb1", runners=[spec])
    # source_ids_refusal requires a source id; the other scenarios still ran
    names = {s.name for s in SCENARIOS if not s.requires_source_id}
    assert set(report["runners"]["fake"]) == names
    assert len(calls) == 2
    prompts = [p for _, p in calls]
    assert any("chat start nb1" in p for p in prompts)
    assert any("research run" in p for p in prompts)
    assert not any("--source-ids" in p for p in prompts)


def test_run_eval_records_unavailable_without_invoking(monkeypatch):
    def boom(spec, prompt, timeout=90):
        raise AssertionError("must not invoke a missing runner")

    monkeypatch.setattr("notelab.research.guard_evals.run_adversary", boom)
    monkeypatch.setattr("notelab.research.guard_evals.detect_runners", lambda c=None: c or [])
    spec = RunnerSpec("ghost", "ghost", ["{prompt}"], available=False, path=None)
    report = run_eval("nb1", source_id="s1", runners=[spec])
    for scenario_name, result in report["runners"]["ghost"].items():
        assert result["status"] == "unavailable"
        assert result["guard_respected"] is None
    assert len(report["runners"]["ghost"]) == len(SCENARIOS)


def test_scenarios_all_read_only_by_construction():
    """Every scenario prompt must run an nlmx path that refuses before touching
    notebook state — the harness's core safety contract."""
    for s in SCENARIOS:
        prompt = s.prompt_template.format(notebook_id="nb", source_id="s")
        assert "nlmx" in prompt and "bare `nlm`" in prompt
