"""
Champion/challenger gating for self-improving skills (Mode A: prompt evolution).

The loop this implements — variant -> eval battery -> gate -> promote/archive —
follows the eval-as-control-plane pattern (see skills/skill-evolution/SKILL.md
for the protocol and references). What varies between versions is PROMPT TEXT
(skill assets, scenario templates); what stays fixed is the battery, so scores
are comparable across versions.

Anti-overfitting rules, enforced by `gate`:
- a challenger must beat the champion by `margin` (default 0.05), not just tie;
- held-out tasks must not regress (no tuning to the battery);
- the tree must be green (`suite_green`) — never promote on a red build.

Scores live in the battery JSON itself (`versions.<v>.scores`), so git history
is the archive: every promotion is a commit, every loser stays inspectable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class TaskScore:
    name: str
    score: float  # 0.0 - 1.0, higher is better
    passed: bool
    detail: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "score": round(self.score, 4),
                "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class BatteryResult:
    version: str
    scores: list[TaskScore] = field(default_factory=list)
    suite_green: bool = True

    @property
    def mean(self) -> float:
        return sum(s.score for s in self.scores) / len(self.scores) if self.scores else 0.0

    @property
    def all_passed(self) -> bool:
        return bool(self.scores) and all(s.passed for s in self.scores)

    def by_name(self) -> dict[str, TaskScore]:
        return {s.name: s for s in self.scores}

    def as_dict(self) -> dict:
        return {"version": self.version, "mean": round(self.mean, 4),
                "suite_green": self.suite_green,
                "scores": [s.as_dict() for s in self.scores]}


#: runner(task_dict) -> TaskScore. Task dicts come straight from the battery
#: JSON, so new task kinds need no gate changes — only a runner that knows them.
RunnerFn = Callable[[dict], TaskScore]


def run_battery(version: str, tasks: list[dict], runner: RunnerFn,
                suite_green: bool = True) -> BatteryResult:
    """Run every battery task through `runner`. A raising runner scores
    0.0/failed for that task — a crashing eval is a failed eval, never a
    skipped one (skips would silently shrink the battery)."""
    scores = []
    for task in tasks:
        try:
            scored = runner(task)
        except Exception as e:  # noqa: BLE001 - scored, not raised; see docstring
            scored = TaskScore(name=task.get("name", "?"), score=0.0,
                               passed=False, detail=f"runner error: {e}")
        scores.append(scored)
    return BatteryResult(version=version, scores=scores, suite_green=suite_green)


@dataclass(frozen=True)
class GateDecision:
    promote: bool
    reasons: list[str]

    def as_dict(self) -> dict:
        return {"promote": self.promote, "reasons": list(self.reasons)}


def cost_rule(delta_S: float, delta_C: float,
              beta0: float = 0.5, beta1: float = 4.0) -> tuple[bool, str]:
    """RRSI's token-budget branch (selection.py): added cost must be paid for
    by measured gain. Budget grows with the gain: tiny wins may not double
    the prompt, large wins may. Within the noise band the margin rule
    already decides, so the gate only applies this above the band."""
    budget = beta0 + beta1 * delta_S
    ok = delta_C <= budget
    return ok, (f"cost change {delta_C:+.3f} {'<=' if ok else '>'} budget "
                f"{budget:.3f} (beta0 {beta0} + beta1 {beta1} * gain "
                f"{delta_S:+.4f})")


def gate(champion: BatteryResult, challenger: BatteryResult,
         margin: float = 0.05,
         held_out: frozenset[str] | set[str] = frozenset(),
         champion_cost: float | None = None,
         challenger_cost: float | None = None,
         beta0: float = 0.5, beta1: float = 4.0) -> GateDecision:
    """Decide whether the challenger dethrones the champion. Every rule
    contributes its reason, so a rejection explains itself. Costs (prompt
    chars, ~tokens/4) are optional: without them the gate is margin +
    held-out + green tree only."""
    reasons = []
    if not challenger.suite_green:
        reasons.append("reject: tree is red (suite_green=false) — never promote on red")
    if not champion.suite_green:
        reasons.append("reject: champion ran on a red tree — scores untrustworthy, re-run")
    gap = challenger.mean - champion.mean
    if gap <= margin:
        reasons.append(f"reject: margin {gap:+.4f} does not beat {margin} "
                       f"(challenger {challenger.mean:.4f} vs champion {champion.mean:.4f})")
    else:
        reasons.append(f"accept: margin {gap:+.4f} clears {margin}")
    if champion_cost and challenger_cost is not None and gap > margin:
        delta_C = (challenger_cost - champion_cost) / champion_cost
        ok, why = cost_rule(gap, delta_C, beta0, beta1)
        reasons.append(("accept: " if ok else "reject: ") + why)
    held_out = frozenset(held_out)
    if held_out:
        old, new = champion.by_name(), challenger.by_name()
        regressed = sorted(n for n in held_out
                           if n in old and n in new and new[n].score < old[n].score)
        missing = sorted(n for n in held_out if n not in old or n not in new)
        if regressed:
            reasons.append(f"reject: held-out regressed: {', '.join(regressed)}")
        elif missing:
            reasons.append(f"reject: held-out tasks missing from a run: {', '.join(missing)}")
        else:
            reasons.append("accept: no held-out regression")
    promote = bool(reasons) and all(r.startswith("accept") for r in reasons)
    return GateDecision(promote=promote, reasons=reasons)


def record_scores(battery_path: str | Path, version: str,
                  result: BatteryResult) -> dict:
    """Archive a version's scores into the battery JSON (git is the archive).
    Returns the updated document. Promotions are separate, deliberate commits —
    this only records evidence, never rewrites the champion pointer... there is
    no champion pointer: the champion is simply the newest version whose
    `promoted` flag is true (see promote())."""
    path = Path(battery_path)
    doc = json.loads(path.read_text())
    versions = doc.setdefault("versions", {})
    entry = versions.setdefault(version, {})
    entry["scores"] = result.as_dict()
    path.write_text(json.dumps(doc, indent=2) + "\n")
    return doc


def promote(battery_path: str | Path, version: str) -> dict:
    """Mark `version` promoted (it already passed gate() — this only stamps).
    Unmarks any previous champion: exactly one version holds the flag."""
    path = Path(battery_path)
    doc = json.loads(path.read_text())
    versions = doc.get("versions", {})
    if version not in versions:
        raise KeyError(f"version {version!r} not in {path} — record scores first")
    for v, entry in versions.items():
        entry["promoted"] = (v == version)
    path.write_text(json.dumps(doc, indent=2) + "\n")
    return doc


def champion_of(doc: dict) -> str | None:
    """Newest promoted version, or None when nothing was ever promoted."""
    return next((v for v in reversed(list(doc.get("versions", {})))
                 if doc["versions"][v].get("promoted")), None)
