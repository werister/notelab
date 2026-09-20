"""
Execute an EvolutionPlan against a notebook — behind an explicit confirm gate.

`plan_evolution` (in evolve.py) only PLANS. This module APPLIES a plan, but the
destructive actions (EVICT, and the drop half of MERGE) run only when the caller
passes `confirm=True`. With confirm=False it is a dry run that reports exactly what
would be removed and touches nothing — that stays the default everywhere, so a source
is never deleted without an explicit human/agent decision.

CREATE/KEEP need no action here: CREATE'd sources were already added by the research
run that produced them, and KEEP means leave as-is. So execution = removals only.

The remover is injected (`Remover` protocol) so this is offline-testable with a fake;
in production it is the nlmx adapter's `remove_source`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .evolve import Action, EvolutionPlan


class Remover(Protocol):
    def remove_source(self, notebook_id: str, source_id: str) -> tuple[bool, str]: ...


@dataclass
class ApplyReport:
    notebook_id: str
    confirmed: bool
    removed: list[dict] = field(default_factory=list)     # actually removed (confirm=True)
    would_remove: list[dict] = field(default_factory=list)  # dry-run preview (confirm=False)
    failures: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"notebook_id": self.notebook_id, "confirmed": self.confirmed,
                "removed": self.removed, "would_remove": self.would_remove,
                "failures": self.failures,
                "summary": {"removed": len(self.removed),
                            "would_remove": len(self.would_remove),
                            "failures": len(self.failures)}}


# Actions that translate to a source removal.
_REMOVE_ACTIONS = {Action.EVICT, Action.MERGE}


def apply_plan(plan: EvolutionPlan, remover: Remover, confirm: bool = False) -> ApplyReport:
    """Apply the removal actions of a plan. confirm=False (default) is a dry run.

    A failed removal is recorded and does not abort the rest — so one un-removable
    source doesn't strand the notebook in a half-applied state silently.
    """
    report = ApplyReport(notebook_id=plan.notebook_id, confirmed=confirm)
    for item in plan.items:
        if item.action not in _REMOVE_ACTIONS:
            continue
        entry = {"action": item.action.value, "source_id": item.source_id,
                 "reason": item.reason}
        if not confirm:
            report.would_remove.append(entry)
            continue
        ok, msg = remover.remove_source(plan.notebook_id, item.source_id)
        if ok:
            report.removed.append({**entry, "message": msg})
        else:
            report.failures.append({**entry, "message": msg})
    return report
