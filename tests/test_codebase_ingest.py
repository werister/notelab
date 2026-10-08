"""Tests for notelab.research.codebase_ingest. Fully offline: local fixtures
plus a mocked urlopen for the GitHub API path."""

import io
import json

import pytest

from notelab.research.codebase_ingest import (
    CodebaseIngester,
    _detect_language,
    _should_skip,
)

SAMPLE_PY = '''"""Sample module."""

import os
from typing import List


class Widget:
    """A widget."""

    def render(self, width):
        return "x" * width


def helper(name: str) -> str:
    return name.upper()
'''


@pytest.fixture()
def sample_repo(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text(SAMPLE_PY)
    (tmp_path / "pkg" / "test_core.py").write_text("def test_x():\n    assert True\n")
    (tmp_path / "README.md").write_text("# Sample\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "junk.py").write_text("x = 1\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG-not-really")
    return tmp_path


def test_local_ingest_extracts_structure(sample_repo):
    digest = CodebaseIngester().ingest(str(sample_repo))

    by_path = {f.path: f for f in digest.files}
    assert "pkg/core.py" in by_path
    core = by_path["pkg/core.py"]
    assert core.language == "Python"
    assert {fn["name"] for fn in core.functions} == {"helper"}
    assert [c["name"] for c in core.classes] == ["Widget"]
    assert core.classes[0]["methods"] == ["render"]
    assert any("typing" in imp for imp in core.imports)
    assert core.has_docstrings is True


def test_local_ingest_skips_envs_and_binaries(sample_repo):
    digest = CodebaseIngester().ingest(str(sample_repo))

    paths = [f.path for f in digest.files]
    assert not any(p.startswith(".venv") for p in paths)
    assert "logo.png" not in paths
    assert "README.md" in paths  # docs are kept


def test_digest_stats_and_branch(sample_repo):
    digest = CodebaseIngester().ingest(str(sample_repo))

    assert digest.default_branch == "local"
    assert digest.repo_name == sample_repo.name
    assert digest.languages["Python"] == 2
    # 1 test file out of 3 ingested files (ratio stored rounded to 4dp)
    assert digest.test_file_ratio == pytest.approx(1 / 3, abs=1e-4)
    assert digest.total_lines > 0
    text = digest.to_text()
    assert "pkg/core.py::Widget" in text
    assert "pkg/core.py::helper" in text
    assert "Test files:" in text


def test_source_features_shape(sample_repo):
    features = CodebaseIngester().ingest(str(sample_repo)).to_source_features()

    assert features["id"].startswith("codebase-")
    assert features["url"] == str(sample_repo)
    assert "## API Surface" in features["content"]
    assert features["published"] is None


def test_ingest_is_deterministic(sample_repo):
    first = CodebaseIngester().ingest(str(sample_repo)).to_text()
    second = CodebaseIngester().ingest(str(sample_repo)).to_text()
    assert first == second


def test_max_files_cap(sample_repo):
    digest = CodebaseIngester(max_files=1).ingest(str(sample_repo))
    assert len(digest.files) == 1


def test_should_skip_patterns():
    assert _should_skip("a/node_modules/x.js")
    assert _should_skip("a/.git/config")
    assert _should_skip("a/__pycache__/x.pyc")
    assert _should_skip("a/icon.png")
    assert _should_skip("a/.venv/lib/x.py")
    assert _should_skip(".worktrees/slug/agent/file.py")
    assert not _should_skip("src/main.py")
    assert not _should_skip("README.md")
    assert not _should_skip("Makefile")


def test_detect_language():
    assert _detect_language("a.py") == "Python"
    assert _detect_language("a.ts") == "TypeScript"
    assert _detect_language("a.zzz") == "Other"


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_remote_ingest_mocked(monkeypatch):
    tree = {"tree": [
        {"type": "blob", "path": "pkg/core.py"},
        {"type": "blob", "path": "node_modules/x.js"},
    ]}

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if url.endswith("/repos/o/r"):
            return _FakeResponse(json.dumps({"default_branch": "dev"}).encode())
        if "/git/trees/" in url:
            return _FakeResponse(json.dumps(tree).encode())
        assert url.startswith("https://raw.githubusercontent.com/o/r/dev/")
        return _FakeResponse(SAMPLE_PY.encode())

    monkeypatch.setattr(
        "notelab.research.codebase_ingest.urllib.request.urlopen", fake_urlopen
    )
    digest = CodebaseIngester().ingest("https://github.com/o/r")

    assert digest.default_branch == "dev"
    assert [f.path for f in digest.files] == ["pkg/core.py"]
    assert digest.files[0].functions[0]["name"] == "helper"


def test_remote_bad_url_raises():
    with pytest.raises(ValueError):
        CodebaseIngester().ingest("https://github.com/onlyowner")


def test_remote_prefers_code_over_docs(monkeypatch):
    tree = {"tree": [
        {"type": "blob", "path": "AAA_README.md"},
        {"type": "blob", "path": "zzz.py"},
    ]}

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if url.endswith("/repos/o/r"):
            return _FakeResponse(json.dumps({}).encode())
        if "/git/trees/" in url:
            return _FakeResponse(json.dumps(tree).encode())
        return _FakeResponse(b"x = 1\n")

    monkeypatch.setattr(
        "notelab.research.codebase_ingest.urllib.request.urlopen", fake_urlopen
    )
    digest = CodebaseIngester(max_files=1).ingest("https://github.com/o/r")

    assert [f.path for f in digest.files] == ["zzz.py"]


def test_remote_fetch_failure_skips_file(monkeypatch):
    tree = {"tree": [{"type": "blob", "path": "a.py"}]}

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if url.endswith("/repos/o/r"):
            return _FakeResponse(json.dumps({}).encode())
        if "/git/trees/" in url:
            return _FakeResponse(json.dumps(tree).encode())
        raise OSError("boom")

    monkeypatch.setattr(
        "notelab.research.codebase_ingest.urllib.request.urlopen", fake_urlopen
    )
    digest = CodebaseIngester().ingest("https://github.com/o/r")

    assert digest.files == []
    assert digest.default_branch == "main"


def test_local_missing_path_raises(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        CodebaseIngester().ingest(str(tmp_path / "nope"))


def test_local_file_not_dir_raises(tmp_path):
    f = tmp_path / "one.py"
    f.write_text("x = 1\n")
    with pytest.raises(ValueError, match="not a directory"):
        CodebaseIngester().ingest(str(f))


def test_digest_renders_key_file_classes_and_imports(tmp_path):
    (tmp_path / "main.py").write_text(
        '"""M."""\nimport os\nimport sys\n\n\nclass Engine:\n    """E."""\n\n'
        '    def run(self):\n        """R."""\n        return 1\n'
    )
    text = CodebaseIngester().ingest(str(tmp_path)).to_text()
    assert "**Classes:**" in text
    assert "`Engine`(run)" in text
    assert "**Imports:** os, sys" in text


def test_digest_notes_extra_key_files(tmp_path):
    for i in range(11):
        (tmp_path / f"test_{i}.py").write_text("def t():\n    pass\n")
    text = CodebaseIngester().ingest(str(tmp_path)).to_text()
    assert "+ 1 more key files not shown" in text


def test_should_skip_extension_gate():
    from notelab.research.codebase_ingest import _should_skip
    assert _should_skip("data/file.xyz") is True
    assert _should_skip("data/file.TXT") is False
    assert _should_skip("noextension") is False


def test_comment_ratio_edges():
    from notelab.research.codebase_ingest import _comment_ratio
    assert _comment_ratio("") == 0.0
    assert _comment_ratio("# a\ncode\n// b\nmore\n") == 0.5


def test_git_suffix_stripped_from_repo_name(tmp_path):
    repo = tmp_path / "myrepo.git"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    digest = CodebaseIngester().ingest(str(repo))
    assert digest.repo_name == "myrepo"


def test_local_long_file_truncated_to_cap(tmp_path):
    (tmp_path / "big.py").write_text("\n".join(f"x{i} = {i}" for i in range(50)))
    digest = CodebaseIngester(max_file_lines=5).ingest(str(tmp_path))
    assert digest.files[0].lines == 5


def test_local_unreadable_file_skipped(tmp_path):
    import os
    import pytest
    # root bypasses POSIX file permissions, so chmod(0) doesn't make a file unreadable
    # for the process -- the skip-on-read-error path can't be exercised as root.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("cannot test unreadable-file skip as root (root ignores chmod perms)")
    good = tmp_path / "good.py"
    good.write_text("x = 1\n")
    bad = tmp_path / "bad.py"
    bad.write_text("y = 2\n")
    bad.chmod(0o000)
    try:
        digest = CodebaseIngester().ingest(str(tmp_path))
    finally:
        bad.chmod(0o644)
    assert [f.path for f in digest.files] == ["good.py"]


def test_remote_long_file_truncated_to_cap(monkeypatch):
    tree = {"tree": [{"type": "blob", "path": "big.py"}]}
    body = ("\n".join(f"x{i} = {i}" for i in range(50))).encode()

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if url.endswith("/repos/o/r"):
            return _FakeResponse(json.dumps({}).encode())
        if "/git/trees/" in url:
            return _FakeResponse(json.dumps(tree).encode())
        return _FakeResponse(body)

    monkeypatch.setattr(
        "notelab.research.codebase_ingest.urllib.request.urlopen", fake_urlopen
    )
    digest = CodebaseIngester(max_file_lines=5).ingest("https://github.com/o/r")
    assert digest.files[0].lines == 5


def test_check_docstrings_js_and_go():
    ing = CodebaseIngester()
    assert ing._check_docstrings("/** doc */\nconst x = 1;", "JavaScript") is True
    assert ing._check_docstrings("const x = 1;", "JavaScript") is False
    assert ing._check_docstrings("// c\nfunc main() {}", "Go") is True
    assert ing._check_docstrings("func main() {}", "Go") is False
