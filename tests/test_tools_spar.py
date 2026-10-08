"""spar() must dispatch each family through its OWN CLI (run_adversary), not the
shared NotebookLM path -- fixes FIELD-NOTES.md gap #9. Offline: spar() resolves its
runners via detect_runners_with_reliability() (PATH detection + the external
reliability filter), and run_adversary, both monkeypatched here, no real CLI, no
real reliability ledger, no NotebookLM call."""
import json

import notelab._tools as tools_mod
from notelab._tools import ToolLogic
from notelab.research.evals import RunnerResult, RunnerSpec


class _FakeStore:
    def record_sparring(self, result):
        return "sparring-1"


def test_spar_dispatches_each_family_through_its_own_cli(monkeypatch):
    specs = [
        RunnerSpec("opencode", "opencode", ["run", "{prompt}"], available=True, path="/x/opencode"),
        RunnerSpec("cline", "cline", ["--auto-approve", "false", "{prompt}"],
                  available=True, path="/x/cline"),
    ]
    monkeypatch.setattr(tools_mod, "detect_runners_with_reliability", lambda: specs)

    calls = []

    def fake_run_adversary(spec, prompt, timeout=180, cwd=None):
        calls.append(spec.name)
        return RunnerResult(spec.name, True, 0,
                            stdout=f"{spec.name} says: see https://example.com/{spec.name}",
                            stderr="")

    monkeypatch.setattr(tools_mod, "run_adversary", fake_run_adversary)

    tool = ToolLogic(adapter=object(), store=_FakeStore())
    out = json.loads(tool.spar("question", "prompt"))

    # both families were ACTUALLY dispatched -- this is the regression check for
    # gap #9 (every family previously routed through the identical NotebookLM path
    # regardless of which CLI was "detected")
    assert sorted(calls) == ["cline", "opencode"]

    by_family = {r["family"]: r for r in out["results"]}
    assert by_family["opencode"]["available"] is True
    assert by_family["cline"]["available"] is True
    # each family's OWN url shows up in its own report, not a shared/identical one
    opencode_dump = json.dumps(by_family["opencode"])
    cline_dump = json.dumps(by_family["cline"])
    assert "example.com/opencode" in opencode_dump
    assert "example.com/opencode" not in cline_dump
    assert "example.com/cline" in cline_dump


def test_spar_records_unavailable_family_without_calling_run_adversary(monkeypatch):
    specs = [RunnerSpec("kilo", "kilo", ["run", "{prompt}"], available=False, path=None)]
    monkeypatch.setattr(tools_mod, "detect_runners_with_reliability", lambda: specs)

    def fail_if_called(*a, **k):
        raise AssertionError("run_adversary must not be called for an unavailable family")

    monkeypatch.setattr(tools_mod, "run_adversary", fail_if_called)

    tool = ToolLogic(adapter=object(), store=_FakeStore())
    out = json.loads(tool.spar("q", "p"))
    assert out["results"][0]["available"] is False
    # no skipped_reason was set on this spec -- generic fallback text is correct here
    assert out["results"][0]["error"] == "kilo not installed"


def test_spar_surfaces_reliability_skip_reason_not_generic_not_installed(monkeypatch):
    """A family that IS on PATH but was excluded by the external reliability filter
    (detect_runners_with_reliability marks it available=False with a skipped_reason,
    e.g. quota exhaustion reported in the reliability ledger) must surface THAT reason
    in spar()'s output, not the generic 'not installed' text -- the whole point of the
    reliability filter is to tell the caller WHY a family was skipped."""
    specs = [RunnerSpec("codex", "codex", ["exec", "{prompt}"], available=False,
                        path="/usr/bin/codex", provider="codex",
                        skipped_reason="recent quota exhaustion reported in the reliability ledger")]
    monkeypatch.setattr(tools_mod, "detect_runners_with_reliability", lambda: specs)

    def fail_if_called(*a, **k):
        raise AssertionError("run_adversary must not be called for an unavailable family")

    monkeypatch.setattr(tools_mod, "run_adversary", fail_if_called)

    tool = ToolLogic(adapter=object(), store=_FakeStore())
    out = json.loads(tool.spar("q", "p"))
    assert out["results"][0]["available"] is False
    assert "quota" in out["results"][0]["error"].lower()
    assert out["results"][0]["error"] != "codex not installed"


def test_spar_surfaces_error_when_returncode_is_none_not_zero(monkeypatch):
    # run_adversary returns returncode=None (not just on a timeout) on a Popen/
    # communicate() exception path -- `if result.returncode` alone can't tell that
    # apart from a clean returncode=0 success, since both are falsy. This must
    # surface the real stderr, not silently report a clean empty success.
    specs = [RunnerSpec("muse", "muse", ["exec", "{prompt}"], available=True, path="/x/muse")]
    monkeypatch.setattr(tools_mod, "detect_runners_with_reliability", lambda: specs)

    def fake_run_adversary(spec, prompt, timeout=180, cwd=None):
        return RunnerResult(spec.name, True, None, stdout="", stderr="FileNotFoundError: gone")

    monkeypatch.setattr(tools_mod, "run_adversary", fake_run_adversary)

    tool = ToolLogic(adapter=object(), store=_FakeStore())
    out = json.loads(tool.spar("q", "p"))
    assert out["results"][0]["error"] == "FileNotFoundError: gone"


def test_spar_surfaces_skipped_reason_instead_of_reporting_success(monkeypatch):
    specs = [RunnerSpec("kiro-cli", "kiro-cli", [], available=True, path="/x/kiro-cli")]
    monkeypatch.setattr(tools_mod, "detect_runners_with_reliability", lambda: specs)

    def fake_run_adversary(spec, prompt, timeout=180, cwd=None):
        return RunnerResult(spec.name, True, None, skipped_reason="no {prompt} placeholder")

    monkeypatch.setattr(tools_mod, "run_adversary", fake_run_adversary)

    tool = ToolLogic(adapter=object(), store=_FakeStore())
    out = json.loads(tool.spar("q", "p"))
    assert out["results"][0]["error"] == "no {prompt} placeholder"
