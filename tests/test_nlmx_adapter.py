"""Offline tests for NlmxAdapter — the ONLY place the server touches NotebookLM.

Every test stubs `_run` (no nlmx binary, no network). Each pins either an argv
shape passed to `nlmx` or a parsing/error behavior of the adapter.
"""
import json
import subprocess

import pytest

from notelab.nlmx_adapter import NlmxAdapter, NlmxError, _parse_date, _resolve_nlmx
from notelab.research.ab import Strategy


class FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _adapter(monkeypatch, responses):
    """NlmxAdapter with _run stubbed: responses consumed in order, calls recorded."""
    calls = []

    def fake_run(self, args, timeout=None):
        calls.append((args, timeout))
        if responses:
            return responses.pop(0)
        return FakeProc()

    monkeypatch.setattr(NlmxAdapter, "_run", fake_run)
    return NlmxAdapter(bin_path="/fake/nlmx"), calls


# --- binary resolution ------------------------------------------------------

def test_resolve_prefers_env(monkeypatch, tmp_path):
    fake = tmp_path / "nlmx"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setenv("NLMX_BIN", str(fake))
    assert _resolve_nlmx() == str(fake)


def test_resolve_falls_back_to_path_then_home(monkeypatch):
    monkeypatch.delenv("NLMX_BIN", raising=False)
    monkeypatch.setattr("notelab.nlmx_adapter.shutil.which", lambda _c: "/path/nlmx")
    assert _resolve_nlmx() == "/path/nlmx"

    monkeypatch.setattr("notelab.nlmx_adapter.shutil.which", lambda _c: None)
    monkeypatch.setattr("notelab.nlmx_adapter.os.path.exists", lambda _p: True)
    monkeypatch.setattr("notelab.nlmx_adapter.os.path.expanduser",
                        lambda _p: "/home/u/.local/bin/nlmx")
    assert _resolve_nlmx() == "/home/u/.local/bin/nlmx"


def test_resolve_missing_raises_clear_error(monkeypatch):
    monkeypatch.delenv("NLMX_BIN", raising=False)
    monkeypatch.setattr("notelab.nlmx_adapter.shutil.which", lambda _c: None)
    monkeypatch.setattr("notelab.nlmx_adapter.os.path.exists", lambda _p: False)
    with pytest.raises(NlmxError, match="nlmx not found"):
        _resolve_nlmx()


def test_run_timeout_becomes_nlmx_error(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="nlmx", timeout=5)

    monkeypatch.setattr("notelab.nlmx_adapter.subprocess.run", boom)
    with pytest.raises(NlmxError, match="timed out after 5s"):
        NlmxAdapter(bin_path="/fake/nlmx")._run(["notebook", "list"])


# --- create_notebook --------------------------------------------------------

def test_create_notebook_parses_id_from_second_line(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [
        FakeProc(stdout="✓ Created notebook: T\n  ID: abc-123\n")])
    assert adapter.create_notebook("T") == "abc-123"
    # title is POSITIONAL — nlm has no --title flag
    assert calls[0][0] == ["notebook", "create", "T"]


def test_create_notebook_failure_raises(monkeypatch):
    adapter, _ = _adapter(monkeypatch, [FakeProc(returncode=1, stderr="nope")])
    with pytest.raises(NlmxError, match="nope"):
        adapter.create_notebook("T")


def test_create_notebook_unparseable_id_raises(monkeypatch):
    adapter, _ = _adapter(monkeypatch, [FakeProc(stdout="created, trust me\n")])
    with pytest.raises(NlmxError, match="no parseable id"):
        adapter.create_notebook("T")


# --- list_notebooks ---------------------------------------------------------

def test_list_notebooks_accepts_list_or_mapping(monkeypatch):
    adapter, _ = _adapter(monkeypatch, [
        FakeProc(stdout=json.dumps([{"id": "a", "title": "T"}]))])
    assert adapter.list_notebooks() == [{"id": "a", "title": "T"}]

    adapter, _ = _adapter(monkeypatch, [
        FakeProc(stdout=json.dumps({"notebooks": [{"notebook_id": "b", "name": "U"}]}))])
    assert adapter.list_notebooks() == [{"id": "b", "title": "U"}]


def test_list_notebooks_empty_or_garbage_is_empty_list(monkeypatch):
    adapter, _ = _adapter(monkeypatch, [FakeProc(stdout=""), FakeProc(stdout="hi")])
    assert adapter.list_notebooks() == []
    assert adapter.list_notebooks() == []


def test_list_notebooks_failure_raises(monkeypatch):
    adapter, _ = _adapter(monkeypatch, [FakeProc(returncode=1, stderr="auth")])
    with pytest.raises(NlmxError, match="auth"):
        adapter.list_notebooks()


# --- sources ----------------------------------------------------------------

def test_list_sources_failure_raises(monkeypatch):
    adapter, _ = _adapter(monkeypatch, [FakeProc(returncode=1, stderr="gone")])
    with pytest.raises(NlmxError, match="gone"):
        adapter.list_sources("nb1")


def test_source_content_empty_on_failure(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [
        FakeProc(stdout="body"), FakeProc(returncode=1)])
    assert adapter.source_content("s1") == "body"
    assert adapter.source_content("s2") == ""
    assert calls[0][0] == ["source", "content", "s1"]


def test_add_source_failure_and_titleless(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [FakeProc(returncode=1, stderr="quota")])
    ok, msg = adapter.add_source("nb1", "text")
    assert (ok, msg) == (False, "quota")
    assert calls[0][0] == ["source", "add", "nb1", "--text", "text"]


def test_remove_source_uses_delete_with_confirm(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [FakeProc(stdout="removed")])
    ok, msg = adapter.remove_source("nb1", "s9")
    assert ok is True
    # `nlm source delete` takes only source ids, no notebook scope
    assert calls[0][0] == ["source", "delete", "s9", "--confirm"]


# --- research_run -----------------------------------------------------------

def test_research_run_uses_given_notebook_and_backfills(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [
        FakeProc(stdout="Task ID: t1\n"),  # research run
        FakeProc(stdout=json.dumps([{"id": "s1", "title": "T"}])),  # source list
        FakeProc(stdout="full text"),  # content backfill
    ])
    sources, meta = adapter.research_run(Strategy("a", "q", notebook_id="nb1"))
    assert meta["notebook_id"] == "nb1"
    assert meta["task_id"] == "t1"
    assert sources[0].content == "full text"
    run_call = calls[0][0]
    assert run_call[:3] == ["research", "run", "q"]
    assert "--notebook-id" in run_call and "nb1" in run_call


def test_research_run_creates_notebook_when_missing(monkeypatch):
    adapter, calls = _adapter(monkeypatch, [
        FakeProc(stdout="✓ Created notebook: x\n  ID: nb-new\n"),  # create
        FakeProc(stdout="Task ID: t2\n"),  # research run
        FakeProc(stdout="[]"),  # source list
    ])
    _, meta = adapter.research_run(Strategy("a", "q"))
    assert meta["notebook_id"] == "nb-new"
    assert calls[0][0][:2] == ["notebook", "create"]


def test_research_run_error_returns_empty_with_meta(monkeypatch):
    adapter, _ = _adapter(monkeypatch, [FakeProc(returncode=1, stderr="bad mode")])
    sources, meta = adapter.research_run(Strategy("a", "q", notebook_id="nb1"))
    assert sources == []
    assert meta["error"] == "bad mode"


# --- _parse_sources ---------------------------------------------------------

def test_parse_sources_field_aliases_and_skips():
    from notelab.nlmx_adapter import NlmxAdapter as A
    raw = json.dumps([
        {"source_id": "s1", "name": "N", "source_url": "U", "text": "C",
         "date": "2026-01-02"},
        "not-a-dict",
        {"title": "only-title"},
    ])
    out = A._parse_sources(raw)
    assert len(out) == 2
    assert (out[0].id, out[0].title, out[0].url, out[0].content) == ("s1", "N", "U", "C")
    assert out[0].published is not None
    assert out[1].id == "only-title"  # title as id fallback


def test_parse_sources_empty_or_garbage():
    from notelab.nlmx_adapter import NlmxAdapter as A
    assert A._parse_sources("") == []
    assert A._parse_sources("not json") == []
    assert A._parse_sources(json.dumps({"sources": []})) == []


# --- _parse_date ------------------------------------------------------------

@pytest.mark.parametrize("raw", ["2026-01-02T03:04:05+0000", "2026-01-02T03:04:05Z",
                                 "2026-01-02", "2026/01/02"])
def test_parse_date_formats(raw):
    d = _parse_date(raw)
    assert (d.year, d.month, d.day) == (2026, 1, 2)


def test_parse_date_rejects_garbage():
    assert _parse_date(None) is None
    assert _parse_date("") is None
    assert _parse_date("yesterday-ish") is None
