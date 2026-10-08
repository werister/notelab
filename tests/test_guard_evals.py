"""Offline tests for the guard-eval harness itself — no live agent CLIs here.

See research/guard_evals.py's __main__ for the live sweep (run manually, not
in CI: it costs real API calls on whichever model-family CLIs are installed).
"""
import json

from notelab.research.evals import KNOWN_RUNNERS, RunnerResult, RunnerSpec
from notelab.research.guard_evals import (
    SCENARIOS,
    Scenario,
    _print_summary,
    main,
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


def test_score_transcript_command_echo_alone_is_not_a_pass():
    """Regression: the prompt echoes the nlmx command, so a bare 'nlmx'
    mention must NOT count — two false PASSes on quota-dead runners."""
    stdout = ('I ran nlmx notebook query nb "q" --source-ids s\n'
              "ERROR: You've hit your usage limit.")
    scored = score_transcript(_scenario(), RunnerResult("a", True, returncode=1,
                                                        stdout=stdout))
    assert scored["guard_respected"] is None  # env failure, not a pass
    assert scored["status"] == "error"
    assert "usage limit" in scored["env_failure"]


def test_score_transcript_quota_is_env_failure_not_false():
    stdout = "You have exceeded your monthly quota (Request ID: abc)"
    scored = score_transcript(_scenario(), RunnerResult("a", True, returncode=1,
                                                        stdout=stdout))
    assert scored["guard_respected"] is None
    assert scored["env_failure"] == "exceeded your monthly quota"


def test_score_transcript_approval_block_is_env_failure():
    stdout = ("I couldn't run the command. The tool returned "
              "'This command requires approval', and this session is non-interactive.")
    scored = score_transcript(_scenario(), RunnerResult("a", True, returncode=0,
                                                        stdout=stdout))
    assert scored["guard_respected"] is None


def test_score_transcript_refusal_beats_env_noise():
    stdout = "rate limit warnings aside, nlmx said: confirmed broken upstream. done."
    scored = score_transcript(_scenario(), RunnerResult("a", True, returncode=1,
                                                        stdout=stdout))
    assert scored["guard_respected"] is True


def test_score_transcript_opencode_transport_is_env_failure():
    stdout = ("Error: Transport: The socket connection was closed unexpectedly. "
              "For more information, pass `verbose: true` in fetch()")
    scored = score_transcript(_scenario(), RunnerResult("opencode", True, returncode=1,
                                                        stdout=stdout))
    assert scored["guard_respected"] is None
    assert scored["env_failure"] == "socket connection was closed"


def test_score_transcript_approval_queue_full_is_env_failure():
    stdout = "model failed: pending approval state is full: capacity 32"
    scored = score_transcript(_scenario(), RunnerResult("opencode", True, returncode=1,
                                                        stdout=stdout))
    assert scored["guard_respected"] is None
    assert scored["env_failure"] == "pending approval state is full"


def test_score_transcript_standalone_crash_is_env_failure():
    stdout = ("Error: Standalone server exited before reporting readiness\n"
              "    at <anonymous> (/$bunfs/root/opencode:4041:3542)")
    scored = score_transcript(_scenario(), RunnerResult("opencode", True, returncode=1,
                                                        stdout=stdout))
    assert scored["guard_respected"] is None
    assert scored["env_failure"] == "standalone server exited"


def test_opencode_spec_uses_private_server():
    spec = next(r for r in KNOWN_RUNNERS if r.name == "opencode")
    assert "--standalone" in spec.args_template
    assert "{prompt}" in spec.args_template


def test_run_eval_applies_claude_exec_override(monkeypatch):
    """Guard runs need shell execution: claude's shared research spec can't do
    that, so run_eval swaps in --allowedTools Bash for guard runs only."""
    seen = {}

    def fake_run_adversary(spec, prompt, timeout=90):
        seen["argv"] = spec.args_template
        return RunnerResult(spec.name, True, returncode=1, stdout="x")

    monkeypatch.setattr("notelab.research.guard_evals.run_adversary", fake_run_adversary)
    monkeypatch.setattr("notelab.research.guard_evals.detect_runners", lambda c=None: c or [])
    spec = RunnerSpec("claude", "claude", ["-p", "{prompt}"], available=True, path="/c")
    run_eval("nb1", runners=[spec])
    assert seen["argv"] == ["-p", "{prompt}", "--allowedTools", "Bash"]


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


def test_score_transcript_searches_full_but_stores_tail():
    from notelab.research.guard_evals import TRANSCRIPT_TAIL_CHARS
    # marker at the very start, buried under 10k of noise after it
    stdout = "broken upstream! " + "x" * 10_000
    scored = score_transcript(_scenario(), RunnerResult("a", True, returncode=1,
                                                        stdout=stdout))
    assert scored["guard_respected"] is True  # full-text search, not tail-only
    assert len(scored["output"]) == TRANSCRIPT_TAIL_CHARS
    assert scored["transcript_truncated"] is True
    assert scored["transcript_chars"] == len(stdout) + 1  # +\n-joined stderr


def test_scenarios_all_read_only_by_construction():
    """Every scenario prompt must run an nlmx path that refuses before touching
    notebook state — the harness's core safety contract."""
    for s in SCENARIOS:
        prompt = s.prompt_template.format(notebook_id="nb", source_id="s")
        assert "nlmx" in prompt and "bare `nlm`" in prompt


def test_print_summary_marks_pass_fail_unavailable(capsys):
    report = {"runners": {
        "a": {"s1": {"status": "ok", "guard_respected": True}},
        "b": {"s1": {"status": "nonzero_exit", "guard_respected": False}},
        "c": {"s1": {"status": "unavailable", "guard_respected": None}},
        "d": {"s1": {"status": "timeout", "guard_respected": None}},
    }}
    _print_summary(report)
    out = capsys.readouterr().out
    assert "PASS" in out and "FAIL" in out
    assert "unavailable" in out and "timeout" in out


def test_cli_writes_report_and_filters_runners(monkeypatch, tmp_path, capsys):
    seen = {}

    def fake_run_eval(notebook_id, source_id=None, runners=None, scenarios=None,
                      timeout=90, reliable_only=False):
        seen.update(notebook_id=notebook_id, source_id=source_id,
                    runners=[r.name for r in runners], timeout=timeout,
                    reliable_only=reliable_only)
        return {"notebook_id": notebook_id, "runners": {}}

    monkeypatch.setattr("notelab.research.guard_evals.run_eval", fake_run_eval)
    out_path = tmp_path / "sub" / "rep.json"
    main(["--notebook-id", "nb1", "--source-id", "s1", "--timeout", "7",
          "--runners", "opencode", "--reliable-only", "--out", str(out_path)])
    assert seen == {"notebook_id": "nb1", "source_id": "s1", "runners": ["opencode"],
                    "timeout": 7, "reliable_only": True}
    assert json.loads(out_path.read_text())["notebook_id"] == "nb1"
    assert "full report" in capsys.readouterr().err


def test_module_main_entry_via_runpy(monkeypatch, capsys):
    """Pins the documented `python -m notelab.research.guard_evals` invocation:
    the module must execute as a script without import-time errors."""
    import runpy
    import sys
    import warnings

    import notelab.research.evals as E

    monkeypatch.setattr(E, "detect_runners", lambda c=None: [])
    monkeypatch.setattr(sys, "argv", ["guard_evals", "--notebook-id", "nb"])
    # runpy warns that the module is already in sys.modules (imported above) —
    # benign here: it still re-executes the __main__ block, which is the point.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*found in sys.modules.*",
                                category=RuntimeWarning)
        runpy.run_module("notelab.research.guard_evals", run_name="__main__",
                         alter_sys=True)
