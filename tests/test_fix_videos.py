"""research_fix_videos: plan flags bot-wall YouTube copies; apply adds native copies; delete is opt-in."""
import json

from notelab._tools import ToolLogic, tool_names
from notelab.research.quality import SourceFeatures
from notelab.storage import Store

WALL = "Sign in to confirm you're not a bot"


class Stub:
    def __init__(self, sources):
        self.sources = sources; self.added = []; self.removed = []
    def list_sources(self, nb): return self.sources
    def source_content(self, sid): return ""
    def add_youtube(self, nb, url): self.added.append(url); return True, "ok"
    def remove_source(self, nb, sid): self.removed.append(sid); return True, "ok"


def _logic(tmp_path, sources):
    stub = Stub(sources)
    return stub, ToolLogic(adapter=stub, store=Store(tmp_path / "s.db"))


def _src():
    return [SourceFeatures("a", "Video A", "https://www.youtube.com/watch?v=AAA&vl=en", WALL),
            SourceFeatures("b", "Video B", "https://youtu.be/BBB", "real transcript text " * 50),
            SourceFeatures("c", "Blog", "https://example.com/post", "text"),
            SourceFeatures("d", "Video D", "https://www.youtube.com/watch?v=DDD", WALL),
            SourceFeatures("d2", "Video  D", "", "transcript")]  # native copy of D already present (title spacing differs)


def test_registered():
    assert "research_fix_videos" in tool_names()


def test_plan_only_changes_nothing(tmp_path):
    stub, logic = _logic(tmp_path, _src())
    out = json.loads(logic.fix_videos("nb"))
    actions = {r["source_id"]: r["action"] for r in out["plan"]}
    assert actions == {"a": "add_native", "b": "ok", "d": "already_native"}
    assert out["blocked"] == 2 and out["applied"] is None
    assert stub.added == [] and stub.removed == []


def test_apply_adds_native_but_never_deletes_by_default(tmp_path):
    stub, logic = _logic(tmp_path, _src())
    json.loads(logic.fix_videos("nb", apply=True))
    assert stub.added == ["https://www.youtube.com/watch?v=AAA"]  # tracking params stripped
    assert stub.removed == []


def test_remove_junk_only_when_a_native_copy_exists(tmp_path):
    stub, logic = _logic(tmp_path, _src())
    json.loads(logic.fix_videos("nb", apply=True, remove_junk=True))
    assert sorted(stub.removed) == ["a", "d"]  # a: native just added; d: native already there
