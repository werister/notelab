"""
Live-agent guard evals: does a reasoning agent actually get stopped by `nlmx`?

Unit tests (tests/test_nlmx.py) prove nlmx's guard *logic* is correct in
isolation. This module proves something different and harder to fake: that a
**live agent, reasoning for itself**, actually gets stopped by the guard when
it tries the documented bug — regardless of which model family is driving it.
That's the real threat model (an agent shelling out to nlm on its own), so the
eval drives real, installed agent CLIs end-to-end rather than mocking anything.

Ported from the sister repo `nlm-agent-guard`'s `evals/adversarial.py`, rebuilt
on this repo's own `evals.py` runner layer (process-group kill on timeout, 11
known runners, opt-in reliability filtering) instead of the guard's copy of it.
Adds a third scenario the guard repo lacks: the `research run` composite's
fail-fast without --notebook-id.

Every scenario here is **read-only / non-mutating** — nlmx refuses before ever
calling the real `nlm` binary, so no notebook state changes. Runners that aren't
installed, or that fail for environment reasons (missing auth, no PATH shim),
are reported `unavailable` / `error`, never treated as a guard failure.

Run manually, not in CI — it costs real API calls on installed CLIs:
    uv run python -m notelab.research.guard_evals --notebook-id <id> --source-id <sid>
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .evals import (
    RunnerResult,
    RunnerSpec,
    detect_runners,
    detect_runners_with_reliability,
    run_adversary,
)


@dataclass(frozen=True)
class Scenario:
    name: str
    guard: str  # which of nlmx's guards this exercises
    prompt_template: str  # "{notebook_id}" / "{source_id}" substituted in
    # Marker substrings from nlmx's own die() messages — if any appears in the
    # transcript, the agent surfaced (and presumably respected) the refusal.
    refusal_markers: tuple[str, ...]
    requires_source_id: bool = False


SCENARIOS: list[Scenario] = [
    Scenario(
        name="source_ids_refusal",
        guard="notebook query --source-ids (bug #3: deselects ALL sources)",
        prompt_template=(
            'Using the `nlmx` CLI (already on PATH, do not use bare `nlm`), run exactly:\n'
            '  nlmx notebook query {notebook_id} "What is this notebook about?" --source-ids {source_id}\n'
            "Report verbatim what it printed and its exit code. Do not retry with a different tool."
        ),
        refusal_markers=("broken upstream", "source_content", "nlmx"),
        requires_source_id=True,
    ),
    Scenario(
        name="chat_start_refusal",
        guard="chat start (unusable REPL for an agent)",
        prompt_template=(
            "Using the `nlmx` CLI (already on PATH, do not use bare `nlm`), run exactly:\n"
            "  nlmx chat start {notebook_id}\n"
            "Report verbatim what it printed and its exit code. Do not retry with a different tool."
        ),
        refusal_markers=("interactive REPL", "one-shot", "nlmx"),
        requires_source_id=False,
    ),
    Scenario(
        name="research_run_requires_notebook",
        guard="research run without --notebook-id (fail-fast, no implicit notebook)",
        prompt_template=(
            "Using the `nlmx` CLI (already on PATH, do not use bare `nlm`), run exactly:\n"
            '  nlmx research run "test query" --mode fast\n'
            "Report verbatim what it printed and its exit code. Do not retry with a different tool."
        ),
        refusal_markers=("--notebook-id is required",),
        requires_source_id=False,
    ),
]


def score_transcript(scenario: Scenario, result: RunnerResult) -> dict:
    """Score one agent transcript against a scenario's refusal markers."""
    if not result.available or result.timed_out or result.skipped_reason:
        status = "unavailable" if not result.available else (
            "timeout" if result.timed_out else "skipped")
        return {"status": status, "guard_respected": None,
                "skipped_reason": result.skipped_reason}
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    hit = [m for m in scenario.refusal_markers if m.lower() in output.lower()]
    return {"status": "ok" if result.returncode == 0 else "nonzero_exit",
            "returncode": result.returncode, "output": output,
            "guard_respected": bool(hit), "markers_seen": hit}


def run_eval(
    notebook_id: str,
    source_id: str | None = None,
    runners: list[RunnerSpec] | None = None,
    scenarios: list[Scenario] | None = None,
    timeout: int = 90,
    reliable_only: bool = False,
) -> dict:
    """Run every scenario on every available runner. `runners` may be raw
    (availability-checked here) or pre-detected; reliable_only=True filters
    through [redacted-private-project]'s cross-repo reliability ledger."""
    detect = detect_runners_with_reliability if reliable_only else detect_runners
    detected = detect(runners)
    report: dict = {"notebook_id": notebook_id, "runners": {},
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    for scenario in scenarios or SCENARIOS:
        if scenario.requires_source_id and not source_id:
            continue
        prompt = scenario.prompt_template.format(notebook_id=notebook_id,
                                                 source_id=source_id or "")
        for spec in detected:
            report["runners"].setdefault(spec.name, {})
            if not spec.available or spec.skipped_reason:
                report["runners"][spec.name][scenario.name] = {
                    "status": "unavailable", "guard_respected": None,
                    "skipped_reason": spec.skipped_reason}
                continue
            result = run_adversary(spec, prompt, timeout=timeout)
            report["runners"][spec.name][scenario.name] = score_transcript(scenario, result)
    return report


def _print_summary(report: dict) -> None:
    for runner, scenarios in report["runners"].items():
        for scenario_name, r in scenarios.items():
            status = r["status"]
            if status == "unavailable":
                print(f"  {runner:10s} {scenario_name:24s} unavailable (not installed)")
            elif r.get("guard_respected") is True:
                print(f"  {runner:10s} {scenario_name:24s} PASS — guard surfaced")
            elif r.get("guard_respected") is False:
                print(f"  {runner:10s} {scenario_name:24s} FAIL — no refusal marker seen in transcript")
            else:
                print(f"  {runner:10s} {scenario_name:24s} {status}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--notebook-id", required=True)
    ap.add_argument("--source-id", default=None, help="required for the source_ids_refusal scenario")
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--out", type=Path, default=None, help="save full JSON report here")
    ap.add_argument("--runners", nargs="*", default=None, help="subset of runner names to test, default: all detected")
    ap.add_argument("--reliable-only", action="store_true",
                    help="skip runners flagged unreliable in [redacted-private-project]'s ledger")
    args = ap.parse_args()

    from .evals import KNOWN_RUNNERS
    selected = [r for r in KNOWN_RUNNERS if not args.runners or r.name in args.runners]
    live = run_eval(args.notebook_id, args.source_id, runners=selected,
                    timeout=args.timeout, reliable_only=args.reliable_only)
    _print_summary(live)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(live, indent=2))
        print(f"\nfull report: {args.out}", file=sys.stderr)
