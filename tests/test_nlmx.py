"""Unit tests for nlmx's guard logic — the confirmed nlm bugs it intercepts.

All offline: subprocess.run / os.execv are mocked, no real `nlm` or NotebookLM
account is touched. Ported from the sister repo `nlm-agent-guard`'s
tests/test_nlmx.py (same script, same guards), plus coverage for the three
notelab-only hardenings: conditional ledger write, NLM_BIN/PATH resolution,
and the 1200s deep-research wait.
"""
import importlib.util
import json
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

import pytest

NLMX_PATH = Path(__file__).resolve().parent.parent / "nlmx"


@pytest.fixture
def nlmx(tmp_path, monkeypatch):
    """Load the `nlmx` script (no .py extension — installed as a bare
    executable) as an importable module for white-box tests."""
    loader = SourceFileLoader("nlmx_under_test", str(NLMX_PATH))
    spec = importlib.util.spec_from_file_location("nlmx_under_test", NLMX_PATH, loader=loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["nlmx_under_test"] = module
    spec.loader.exec_module(module)

    # Isolate the ledger to a fresh temp file per test — never touch the real
    # ~/.local/state/nlmx/imported.json ledger.
    monkeypatch.setattr(module, "LEDGER_PATH", tmp_path / "imported.json")
    monkeypatch.setattr(module, "NLM", "/fake/bin/nlm")
    yield module
    del sys.modules["nlmx_under_test"]


class FakeProc(SimpleNamespace):
    pass


def fake_run_factory(monkeypatch, responses):
    """responses: list of FakeProc, consumed in call order; records calls."""
    calls = []

    def fake_subprocess_run(args, capture_output=False, text=False):
        calls.append(list(args)[1:])  # drop the nlm binary path for readable assertions
        if responses:
            return responses.pop(0)
        return FakeProc(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    return calls


# ---------------------------------------------------------------------------
# Bug #2: research import is not idempotent upstream — duplicates on re-import
# ---------------------------------------------------------------------------

def test_research_import_first_time_succeeds_and_records_ledger(monkeypatch, nlmx):
    fake_run_factory(monkeypatch, [
        FakeProc(returncode=0, stdout=json.dumps([{"id": "s1"}]), stderr=""),  # before count
        FakeProc(returncode=0),  # the actual import
        FakeProc(returncode=0, stdout=json.dumps([{"id": "s1"}, {"id": "s2"}]), stderr=""),  # after count
    ])
    with pytest.raises(SystemExit) as exc:
        nlmx.cmd_research_import(["nb1", "task1"])
    assert exc.value.code == 0
    ledger = json.loads(nlmx.LEDGER_PATH.read_text())
    assert "nb1:task1" in ledger
    assert ledger["nb1:task1"]["count_before"] == 1
    assert ledger["nb1:task1"]["count_after"] == 2


def test_research_import_repeat_refused_without_force(monkeypatch, nlmx):
    nlmx.save_ledger({"nb1:task1": {"imported_at": "t", "count_before": 1, "count_after": 2}})
    with pytest.raises(SystemExit) as exc:
        nlmx.cmd_research_import(["nb1", "task1"])
    assert exc.value.code == 1


def test_research_import_repeat_allowed_with_force(monkeypatch, nlmx):
    nlmx.save_ledger({"nb1:task1": {"imported_at": "t", "count_before": 1, "count_after": 2}})
    calls = fake_run_factory(monkeypatch, [
        FakeProc(returncode=0, stdout=json.dumps([{"id": "s1"}, {"id": "s2"}]), stderr=""),
        FakeProc(returncode=0),
        FakeProc(returncode=0, stdout=json.dumps([{"id": f"s{i}"} for i in range(4)]), stderr=""),
    ])
    with pytest.raises(SystemExit) as exc:
        nlmx.cmd_research_import(["nb1", "task1", "--force"])
    assert exc.value.code == 0
    # --force must not be forwarded to the real `nlm` binary
    import_call = calls[1]
    assert "--force" not in import_call


def test_research_import_warns_on_apparent_duplicate_bug(monkeypatch, nlmx, capsys):
    fake_run_factory(monkeypatch, [
        FakeProc(returncode=0, stdout=json.dumps([{"id": "s1"}, {"id": "s2"}]), stderr=""),  # before=2
        FakeProc(returncode=0),
        FakeProc(returncode=0, stdout=json.dumps([{"id": f"s{i}"} for i in range(4)]), stderr=""),  # after=4
    ])
    with pytest.raises(SystemExit):
        nlmx.cmd_research_import(["nb1"])
    assert "WARNING" in capsys.readouterr().err


def test_research_import_requires_notebook_id(nlmx):
    with pytest.raises(SystemExit) as exc:
        nlmx.cmd_research_import([])
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# notelab-only: a failed import must NOT poison the ledger against retries
# ---------------------------------------------------------------------------

def test_research_import_failed_not_recorded_retry_allowed(monkeypatch, nlmx, capsys):
    """Exit != 0 AND count unchanged → no ledger entry; a later retry proceeds
    past the already-imported guard instead of dying."""
    def failed_import():
        fake_run_factory(monkeypatch, [
            FakeProc(returncode=0, stdout=json.dumps([{"id": "s1"}]), stderr=""),
            FakeProc(returncode=1, stdout="", stderr="boom"),
            FakeProc(returncode=0, stdout=json.dumps([{"id": "s1"}]), stderr=""),
        ])
        with pytest.raises(SystemExit) as exc:
            nlmx.cmd_research_import(["nb1", "task1"])
        return exc.value.code

    assert failed_import() == 1
    assert "NOT recording in the ledger" in capsys.readouterr().err
    assert not nlmx.LEDGER_PATH.exists()  # nothing recorded

    # retry must reach the real import (exit 1 from the mocked failure), not
    # die in the already-imported guard with the duplicate-source message
    assert failed_import() == 1
    assert "WILL duplicate" not in capsys.readouterr().err


def test_research_import_nonzero_exit_with_growth_still_recorded(monkeypatch, nlmx):
    """FIELD-NOTES gap #7: a successful import can report nonzero exit on an
    HTTP read timeout after the data landed — count growth alone records it."""
    fake_run_factory(monkeypatch, [
        FakeProc(returncode=0, stdout=json.dumps([{"id": "s1"}]), stderr=""),
        FakeProc(returncode=1, stdout="", stderr="read timeout"),
        FakeProc(returncode=0, stdout=json.dumps([{"id": "s1"}, {"id": "s2"}]), stderr=""),
    ])
    with pytest.raises(SystemExit) as exc:
        nlmx.cmd_research_import(["nb1", "task1"])
    assert exc.value.code == 1  # exit code still surfaces the timeout
    ledger = json.loads(nlmx.LEDGER_PATH.read_text())
    assert ledger["nb1:task1"]["count_after"] == 2


# ---------------------------------------------------------------------------
# notelab-only: NLM_BIN env / PATH / ~/.local/bin resolution (no hardcoding)
# ---------------------------------------------------------------------------

def test_resolve_nlm_prefers_env_then_path_then_home(monkeypatch, nlmx, tmp_path):
    fake_bin = tmp_path / "nlm"
    fake_bin.write_text("#!/bin/sh\n")
    monkeypatch.setenv("NLM_BIN", str(fake_bin))
    assert nlmx._resolve_nlm() == str(fake_bin)

    monkeypatch.delenv("NLM_BIN")
    monkeypatch.setattr(nlmx.shutil, "which", lambda _cmd: "/path/nlm")
    assert nlmx._resolve_nlm() == "/path/nlm"

    monkeypatch.setattr(nlmx.shutil, "which", lambda _cmd: None)
    monkeypatch.setattr(nlmx.os.path, "exists", lambda _p: False)
    assert nlmx._resolve_nlm().endswith(".local/bin/nlm")


# ---------------------------------------------------------------------------
# Bug #3: notebook query --source-ids deselects ALL sources instead of scoping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["notebook", "query", "nb1", "q", "--source-ids", "s1"],
    ["query", "notebook", "nb1", "q", "--source-ids", "s1"],
])
def test_source_ids_refused_both_spellings(monkeypatch, nlmx, argv):
    monkeypatch.setattr("sys.argv", ["nlmx", *argv])
    with pytest.raises(SystemExit) as exc:
        nlmx.main()
    assert exc.value.code == 1


def test_query_without_source_ids_passes_through(monkeypatch, nlmx):
    calls = fake_run_factory(monkeypatch, [FakeProc(returncode=0)])
    monkeypatch.setattr("sys.argv", ["nlmx", "notebook", "query", "nb1", "question"])
    with pytest.raises(SystemExit) as exc:
        nlmx.main()
    assert exc.value.code == 0
    assert calls == [["notebook", "query", "nb1", "question"]]


def test_query_with_short_s_flag_is_not_refused(monkeypatch, nlmx):
    """Only --source-ids is broken on `notebook query`; it has no short alias, so a
    bare `-s` must NOT be treated as --source-ids and refused — it passes through."""
    calls = fake_run_factory(monkeypatch, [FakeProc(returncode=0)])
    monkeypatch.setattr("sys.argv", ["nlmx", "notebook", "query", "nb1", "question", "-s", "web"])
    with pytest.raises(SystemExit) as exc:
        nlmx.main()
    assert exc.value.code == 0
    assert calls == [["notebook", "query", "nb1", "question", "-s", "web"]]


# ---------------------------------------------------------------------------
# Bug #4: generation `create` commands block/no-op without --confirm
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["quiz", "create", "nb1"],
    ["create", "quiz", "nb1"],
])
def test_generation_create_auto_injects_confirm(monkeypatch, nlmx, argv):
    calls = []

    def fake_execv(path, args):
        calls.append(args)
        raise SystemExit(0)

    monkeypatch.setattr("os.execv", fake_execv)
    monkeypatch.setattr("sys.argv", ["nlmx", *argv])
    with pytest.raises(SystemExit):
        nlmx.main()
    assert "--confirm" in calls[0]


def test_generation_create_leaves_explicit_confirm_alone(monkeypatch, nlmx):
    calls = []

    def fake_execv(path, args):
        calls.append(args)
        raise SystemExit(0)

    monkeypatch.setattr("os.execv", fake_execv)
    monkeypatch.setattr("sys.argv", ["nlmx", "quiz", "create", "nb1", "--confirm"])
    with pytest.raises(SystemExit):
        nlmx.main()
    assert calls[0].count("--confirm") == 1


# ---------------------------------------------------------------------------
# chat start — refused outright, unusable REPL for an agent
# ---------------------------------------------------------------------------

def test_chat_start_refused(monkeypatch, nlmx):
    monkeypatch.setattr("sys.argv", ["nlmx", "chat", "start", "nb1"])
    with pytest.raises(SystemExit) as exc:
        nlmx.main()
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# research run — new composite: start -> poll -> import, exactly once
# ---------------------------------------------------------------------------

def test_research_run_composite_happy_path(monkeypatch, nlmx):
    calls = fake_run_factory(monkeypatch, [
        FakeProc(returncode=0, stdout="Task ID: abc123\n", stderr=""),  # start
        FakeProc(returncode=0, stdout="status: completed\n", stderr=""),  # status
        FakeProc(returncode=0, stdout=json.dumps([]), stderr=""),  # before-count (import)
        FakeProc(returncode=0),  # import
        FakeProc(returncode=0, stdout=json.dumps([{"id": "s1"}]), stderr=""),  # after-count
    ])
    with pytest.raises(SystemExit) as exc:
        nlmx.cmd_research_run(["find quantum sources", "--notebook-id", "nb1", "--mode", "fast"])
    assert exc.value.code == 0
    assert calls[0][:3] == ["research", "start", "find quantum sources"]
    assert calls[1][:2] == ["research", "status"]
    assert calls[3][:2] == ["research", "import"]


def test_research_run_requires_notebook_id(nlmx):
    with pytest.raises(SystemExit) as exc:
        nlmx.cmd_research_run(["find quantum sources"])
    assert exc.value.code == 1


def test_research_run_dies_if_task_id_unparseable(monkeypatch, nlmx):
    fake_run_factory(monkeypatch, [FakeProc(returncode=0, stdout="no task id here\n", stderr="")])
    with pytest.raises(SystemExit) as exc:
        nlmx.cmd_research_run(["q", "--notebook-id", "nb1"])
    assert exc.value.code == 1


def test_research_run_dies_if_never_completes(monkeypatch, nlmx):
    fake_run_factory(monkeypatch, [
        FakeProc(returncode=0, stdout="Task ID: abc123\n", stderr=""),
        FakeProc(returncode=0, stdout="status: running\n", stderr=""),
    ])
    with pytest.raises(SystemExit) as exc:
        nlmx.cmd_research_run(["q", "--notebook-id", "nb1"])
    assert exc.value.code == 1


@pytest.mark.parametrize("mode,max_wait", [("fast", "60"), ("deep", "1200")])
def test_research_run_max_wait_per_mode(monkeypatch, nlmx, mode, max_wait):
    """600s was measured too short for real deep research (FIELD-NOTES gap #1);
    deep now waits 1200s while fast stays at 60s."""
    calls = fake_run_factory(monkeypatch, [
        FakeProc(returncode=0, stdout="Task ID: abc123\n", stderr=""),
        FakeProc(returncode=0, stdout="status: completed\n", stderr=""),
        FakeProc(returncode=0, stdout=json.dumps([]), stderr=""),
        FakeProc(returncode=0),
        FakeProc(returncode=0, stdout=json.dumps([{"id": "s1"}]), stderr=""),
    ])
    with pytest.raises(SystemExit) as exc:
        nlmx.cmd_research_run(["q", "--notebook-id", "nb1", "--mode", mode])
    assert exc.value.code == 0
    status_call = calls[1]
    assert status_call[status_call.index("--max-wait") + 1] == max_wait


# ---------------------------------------------------------------------------
# Everything else — pure passthrough to real nlm
# ---------------------------------------------------------------------------

def test_unknown_command_passes_through_unmodified(monkeypatch, nlmx):
    calls = []

    def fake_execv(path, args):
        calls.append((path, args))
        raise SystemExit(0)

    monkeypatch.setattr("os.execv", fake_execv)
    monkeypatch.setattr("sys.argv", ["nlmx", "notebook", "list"])
    with pytest.raises(SystemExit):
        nlmx.main()
    assert calls == [(nlmx.NLM, [nlmx.NLM, "notebook", "list"])]


def test_no_args_dies_with_usage(monkeypatch, nlmx):
    monkeypatch.setattr("sys.argv", ["nlmx"])
    with pytest.raises(SystemExit) as exc:
        nlmx.main()
    assert exc.value.code == 1
