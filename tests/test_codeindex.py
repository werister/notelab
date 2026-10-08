"""codeindex (exact def/callers/text, kept versions, diff) + ingest mode='files' bundling."""
import json

from notelab._tools import ToolLogic, tool_names
from notelab.codeindex import CodeIndex, render
from notelab.research.codebase_ingest import CodeFile, bundle_files
from notelab.storage import Store

V1 = '''import os

def make_str(value):
    """Converts a value."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)

class Greeter:
    def hello(self, name):
        return make_str(name)

def main():
    return Greeter().hello("x")
'''
V2 = V1.replace('return value.decode("utf-8")', 'return value.decode("utf-8", "replace")') + '''
def brand_new():
    return make_str(1)
'''


def _repo(root, text):
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.py").write_text(text)
    (root / "README.md").write_text("# demo\nhidden password prompt notes\n")
    return root


def _ci(tmp_path):
    return CodeIndex(tmp_path / "code.db")


def test_def_returns_exact_source(tmp_path):
    ci = _ci(tmp_path)
    ci.index(str(_repo(tmp_path / "r", V1)), name="demo", version="v1")
    hit = ci.find("make_str", "def")[0]
    assert hit["type"] == "def" and hit["path"] == "a.py"
    assert hit["text"] == "\n".join(V1.splitlines()[hit["line"] - 1:hit["end_line"]])
    assert 'return value.decode("utf-8")' in hit["text"]


def test_method_qualname_and_callers(tmp_path):
    ci = _ci(tmp_path)
    ci.index(str(_repo(tmp_path / "r", V1)), name="demo", version="v1")
    assert ci.find("Greeter.hello", "def")[0]["kind"] == "method"
    callers = ci.find("make_str", "callers")
    assert [c["name"] for c in callers] == ["Greeter.hello"]  # AST-accurate: the def line is not a call


def test_subclasses(tmp_path):
    ci = _ci(tmp_path)
    root = tmp_path / "r"; root.mkdir()
    (root / "m.py").write_text("class A: pass\nclass B(A): pass\nclass C(B): pass\nclass D(A): pass\n")
    ci.index(str(root), name="m", version="v1")
    assert [h["name"] for h in ci.find("A", "subclasses")] == ["B", "D"]  # direct only


def test_text_search_and_no_match(tmp_path):
    ci = _ci(tmp_path)
    ci.index(str(_repo(tmp_path / "r", V1)), name="demo", version="v1")
    assert any(h["path"] == "README.md" for h in ci.find("hidden password", "text"))
    assert render(ci.find("parse_toml_config")) == "no matches"


def test_versions_kept_and_diff(tmp_path):
    ci = _ci(tmp_path)
    repo = tmp_path / "r"
    ci.index(str(_repo(repo, V1)), name="demo", version="v1")
    ci.index(str(_repo(repo, V2)), name="demo", version="v2")
    assert [v["version"] for v in ci.versions("demo")] == ["v1", "v2"]
    old = ci.find("make_str", "def", version="v1")[0]["text"]
    new = ci.find("make_str", "def")[0]["text"]          # default = newest
    assert 'decode("utf-8")' in old and '"replace"' in new  # the old version is not forgotten
    d = ci.diff("demo", "v1", "v2")
    assert d["symbols_added"] == ["a.py::brand_new"]
    assert d["symbols_changed"] == ["a.py::make_str"]
    assert d["files_changed"] == ["a.py"] and d["symbols_removed"] == []


def test_reindex_same_version_replaces_not_duplicates(tmp_path):
    ci = _ci(tmp_path)
    r = str(_repo(tmp_path / "r", V1))
    ci.index(r, name="demo", version="v1"); ci.index(r, name="demo", version="v1")
    assert len(ci.versions("demo")) == 1 and len(ci.find("make_str", "def", version="*")) == 1


def test_non_python_symbols(tmp_path):
    root = tmp_path / "js"; root.mkdir()
    (root / "x.js").write_text("export function doThing(a) {\n  return helper(a);\n}\nfunction helper(b) { return b }\n")
    ci = _ci(tmp_path)
    ci.index(str(root), name="js", version="v1")
    assert ci.find("doThing", "def")[0]["line"] == 1
    assert ci.find("helper", "callers")[0]["name"] == "doThing"


def test_bundle_files_headers_and_split():
    files = [CodeFile("a.py", "Python", "x = 1\n"), CodeFile("b.py", "Python", "\n".join(f"l{i} = {i}" for i in range(400)))]
    one = bundle_files(files, limit=90_000)
    assert len(one) == 1 and "# FILE: a.py" in one[0][1] and "# FILE: b.py" in one[0][1]
    many = bundle_files(files, limit=1500)
    assert len(many) > 1 and all(len(t) <= 1600 for _, t in many)
    assert any("(part 1/" in t for _, t in many)
    assert "".join(t for _, t in many).count("l399 = 399") == 1  # nothing dropped or duplicated


class _Stub:
    def __init__(self): self.added = []
    def create_notebook(self, title): return "nb-new"
    def add_source(self, nb, text, title=""):
        self.added.append((nb, text, title)); return True, "ok"


def test_ingest_files_mode_adds_real_source_and_respects_cap(tmp_path):
    repo = _repo(tmp_path / "r", V1)
    stub = _Stub()
    logic = ToolLogic(adapter=stub, store=Store(tmp_path / "s.db"))
    out = json.loads(logic.ingest_codebase(str(repo), mode="files"))
    assert out["sources_added"] >= 1 and "def make_str" in stub.added[0][1]
    capped = json.loads(logic.ingest_codebase(str(repo), mode="files", max_sources=0))
    assert "error" in capped and "code_index" in capped["error"]
    assert "error" in json.loads(logic.ingest_codebase(str(repo), mode="nope"))


def test_tools_registered_and_roundtrip(tmp_path):
    assert {"code_index", "code_find", "code_versions", "code_diff"} <= set(tool_names())
    logic = ToolLogic(adapter=_Stub(), store=Store(tmp_path / "s.db"))
    assert "error" in json.loads(logic.code_find("x"))  # nothing indexed yet
    repo = _repo(tmp_path / "r", V1)
    json.loads(logic.code_index(str(repo), version="v1"))
    assert "def make_str" in logic.code_find("make_str", "def")
    assert json.loads(logic.code_versions())[0]["version"] == "v1"
