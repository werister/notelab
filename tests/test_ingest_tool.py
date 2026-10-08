"""research_ingest_codebase tool + NlmxAdapter.add_source.

Drives ToolLogic with a stub adapter (no nlmx/network) and a tiny fixture
repo on disk for the real CodebaseIngester.
"""
import json
import tempfile
from pathlib import Path

from notelab._tools import ToolLogic, tool_names
from notelab.nlmx_adapter import NlmxAdapter
from notelab.research.codebase_ingest import CodebaseDigest, CodebaseIngester, CodeFile
from notelab.storage import Store


def _fixture_repo(root: Path) -> Path:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text('"""pkg."""\n')
    (root / "pkg" / "core.py").write_text(
        '"""Core."""\nimport os\n\n'
        'class Engine:\n    """Run things."""\n'
        '    def run(self, x):\n        """Run one."""\n        return x\n\n'
        'def helper(a, b):\n    """Add."""\n    return a + b\n'
    )
    (root / "README.md").write_text("# demo\n")
    return root


class StubAdapter:
    def __init__(self):
        self.added = []
        self.created = []

    def create_notebook(self, title):
        self.created.append(title)
        return "nb-new"

    def add_source(self, notebook_id, text, title=""):
        self.added.append((notebook_id, text, title))
        return True, "added"


def _logic(stub, tmp_path):
    return ToolLogic(adapter=stub, store=Store(tmp_path / "ing.db"))


def test_tool_registered():
    assert "research_ingest_codebase" in tool_names()


def test_ingest_local_repo_adds_digest_source(tmp_path):
    repo = _fixture_repo(tmp_path / "repo")
    stub = StubAdapter()
    out = json.loads(_logic(stub, tmp_path).ingest_codebase(str(repo), notebook_id="nb1"))
    assert out["added"] is True
    assert out["notebook_id"] == "nb1"
    assert out["repo_name"] == "repo"
    assert out["files"] >= 2
    assert stub.created == []  # explicit notebook: no auto-create
    nb, text, title = stub.added[0]
    assert nb == "nb1"
    assert "Engine" in text and "helper" in text
    assert "repo" in title


def test_ingest_auto_creates_notebook(tmp_path):
    repo = _fixture_repo(tmp_path / "repo")
    stub = StubAdapter()
    out = json.loads(_logic(stub, tmp_path).ingest_codebase(str(repo)))
    assert out["notebook_id"] == "nb-new"
    assert stub.created == ["[codebase] repo"]


def test_ingest_invalid_repo_is_json_error(tmp_path):
    stub = StubAdapter()
    out = json.loads(_logic(stub, tmp_path).ingest_codebase("https://not-github.example/x"))
    assert "error" in out
    assert stub.added == []


def test_ingest_missing_local_path_is_json_error(tmp_path):
    stub = StubAdapter()
    out = json.loads(_logic(stub, tmp_path).ingest_codebase(str(tmp_path / "nope")))
    assert "does not exist" in out["error"]
    assert stub.added == []
    assert stub.created == []


def test_ingest_empty_dir_adds_nothing(tmp_path):
    (tmp_path / "empty").mkdir()
    stub = StubAdapter()
    out = json.loads(_logic(stub, tmp_path).ingest_codebase(
        str(tmp_path / "empty"), notebook_id="nb1"))
    assert "no ingestible files" in out["error"]
    assert stub.added == []


def test_ingest_truncates_huge_digest_visibly(monkeypatch, tmp_path):
    from types import SimpleNamespace

    def huge_ingest(self, repo):
        return SimpleNamespace(
            files=[object()], repo_name="big", default_branch="main",
            total_lines=10, languages={"Python": 1}, test_file_ratio=0.0,
            doc_coverage=0.0, to_text=lambda: "x" * 100_001)

    monkeypatch.setattr(CodebaseIngester, "ingest", huge_ingest)
    stub = StubAdapter()
    out = json.loads(_logic(stub, tmp_path).ingest_codebase(
        "https://github.com/u/big", notebook_id="nb1"))
    assert out["truncated"] is True
    assert out["digest_chars"] > 100_000
    assert stub.added[0][1].endswith("*... digest truncated at 100k chars*")


def test_ingest_nlmx_error_is_json_error(tmp_path):
    from notelab.nlmx_adapter import NlmxError

    class DeadAdapter(StubAdapter):
        def add_source(self, notebook_id, text, title=""):
            raise NlmxError("nlmx gone")

    repo = _fixture_repo(tmp_path / "repo")
    out = json.loads(_logic(DeadAdapter(), tmp_path).ingest_codebase(
        str(repo), notebook_id="nb1"))
    assert out["error"] == "nlmx gone"


def test_ingest_add_failure_reported(tmp_path):
    class FailAdapter(StubAdapter):
        def add_source(self, notebook_id, text, title=""):
            return False, "quota exceeded"

    repo = _fixture_repo(tmp_path / "repo")
    out = json.loads(_logic(FailAdapter(), tmp_path).ingest_codebase(
        str(repo), notebook_id="nb1"))
    assert out["added"] is False
    assert "quota" in out["message"]


def test_adapter_add_source_passes_text_and_title(monkeypatch):
    calls = []

    class FakeProc:
        returncode = 0
        stdout = "added"
        stderr = ""

    def fake_run(self, args, timeout=None):
        calls.append((args, timeout))
        return FakeProc()

    monkeypatch.setattr(NlmxAdapter, "_run", fake_run)
    ok, msg = NlmxAdapter(bin_path="/fake/nlmx").add_source("nb1", "hello", title="T")
    assert ok is True
    assert calls[0][0] == ["source", "add", "nb1", "--text", "hello", "--title", "T"]


def test_digest_truncations_are_visible():
    """to_text() caps key-files/API lists — the cap must say so, not silently drop."""
    files = [CodeFile(path=f"m{i}.py", language="Python", content="x = 1\n",
                      functions=[{"name": f"fn_{i}_{j}"} for j in range(5)])
             for i in range(10)]
    digest = CodebaseDigest(repo_url="https://github.com/u/r", repo_name="r", files=files)
    text = digest.to_text()
    assert "+ 20 more" in text  # 50 API items, 30 shown
