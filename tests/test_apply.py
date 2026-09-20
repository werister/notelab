"""Apply-evolution execution tests — offline, fake remover, confirm gate."""
from notelab.research.apply import apply_plan
from notelab.research.evolve import Action, EvolutionPlan, PlanItem


class FakeRemover:
    def __init__(self, fail_on=None):
        self.calls = []
        self._fail = fail_on or set()

    def remove_source(self, notebook_id, source_id):
        self.calls.append((notebook_id, source_id))
        if source_id in self._fail:
            return False, "not found"
        return True, "removed"


def _plan():
    p = EvolutionPlan(notebook_id="nb")
    p.items = [
        PlanItem(Action.KEEP, "keep1", 0.9, "in budget"),
        PlanItem(Action.CREATE, "new1", 0.8, "novel"),
        PlanItem(Action.MERGE, "dup1", 0.7, "dup", merges_with="keep1"),
        PlanItem(Action.EVICT, "stale1", 0.1, "stale"),
        PlanItem(Action.SKIP, "weak1", 0.1, "weak"),
    ]
    return p


def test_dry_run_removes_nothing():
    remover = FakeRemover()
    rep = apply_plan(_plan(), remover, confirm=False)
    assert remover.calls == []                       # nothing touched
    assert rep.confirmed is False
    ids = {w["source_id"] for w in rep.would_remove}
    assert ids == {"dup1", "stale1"}                 # MERGE + EVICT previewed only
    assert rep.removed == []


def test_confirmed_removes_merge_and_evict_only():
    remover = FakeRemover()
    rep = apply_plan(_plan(), remover, confirm=True)
    removed_ids = {r["source_id"] for r in rep.removed}
    assert removed_ids == {"dup1", "stale1"}         # not keep/create/skip
    assert set(remover.calls) == {("nb", "dup1"), ("nb", "stale1")}
    assert rep.confirmed is True


def test_failed_removal_recorded_not_raised():
    remover = FakeRemover(fail_on={"stale1"})
    rep = apply_plan(_plan(), remover, confirm=True)
    assert {r["source_id"] for r in rep.removed} == {"dup1"}
    assert {f["source_id"] for f in rep.failures} == {"stale1"}
    assert rep.as_dict()["summary"] == {"removed": 1, "would_remove": 0, "failures": 1}


def test_empty_plan_is_noop():
    rep = apply_plan(EvolutionPlan(notebook_id="nb"), FakeRemover(), confirm=True)
    assert rep.removed == [] and rep.would_remove == [] and rep.failures == []
