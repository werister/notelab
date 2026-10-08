#!/usr/bin/env python3
"""Duel a challenger skill version against the champion on a fixed battery.

Live runner for the skill-evolution protocol (see ../SKILL.md). Each battery
task is a (scenario, prompt-template) pair executed by every battery runner
(real agent CLIs via notelab's run_adversary roster); scores are pass rates
over decisive runs. Env failures (quota/auth/no-shell) are no-signal: they
count toward NEITHER passes NOR failures.

Usage:
  python skills/skill-evolution/scripts/evolve.py \\
      --battery skills/skill-evolution/eval/tasks/guard-battery.json \\
      --challenger v2 [--effort smoke|standard|deep] [--promote] [--skip-suite]

  python skills/skill-evolution/scripts/evolve.py --init   # guided setup

Effort matches spend to stakes: smoke (1 runner, cheap signal), standard
(all runners, the default duel), deep (all runners x2 passes, for close
calls). --runners/--scenarios/--repeat override the preset piecemeal.
--promote is refused with --skip-suite: promotion requires the regression gate.

RRSI regulators: a critic screen rejects challengers whose text contains
suite secrets BEFORE any live spend; the margin bar is the max of the
battery margin and a bootstrapped noise floor; a cost rule requires added
prompt chars to be paid for by measured gain; every duel appends one
record to <battery-stem>.history.jsonl.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))

from notelab.research.skill_evolve import (  # noqa: E402
    BatteryResult,
    TaskScore,
    champion_of,
    gate,
    promote,
    record_scores,
)


def load_battery(path):
    doc = json.loads(Path(path).read_text())
    if "versions" not in doc or "held_out" not in doc:
        raise ValueError(f"{path} is not a battery: needs versions + held_out")
    return doc


def score_runs(outcomes):
    """Pass rate over decisive runs; None (no-signal) contributes nothing."""
    decisive = [o for o in outcomes if o is not None]
    no_signal = len(outcomes) - len(decisive)
    if not decisive:
        return 0.0, f"0/0 decisive ({no_signal} no-signal)"
    passes = sum(1 for o in decisive if o)
    return passes / len(decisive), f"{passes}/{len(decisive)} decisive"


EFFORTS = {
    "smoke": {"max_runners": 1, "repeat": 1,
              "blurb": "1 runner, 1 pass — cheap signal, is this idea alive"},
    "standard": {"max_runners": None, "repeat": 1,
                 "blurb": "all runners, 1 pass — the default duel"},
    "deep": {"max_runners": None, "repeat": 2,
             "blurb": "all runners, 2 passes — for close calls and flaky runners"},
}


def resolve_duel_plan(doc, effort="standard", runners=None, scenarios=None,
                      repeat=None):
    """Pick (runners, repeat, scenario-filter) for a duel.

    The effort preset sets the shape; explicit runners/repeat/scenarios
    override it piecemeal. Battery order is runner-preference order, so
    smoke takes the first (cheapest/most reliable) runner.
    """
    if effort not in EFFORTS:
        raise ValueError(f"unknown effort {effort!r}: pick one of {sorted(EFFORTS)}")
    preset = EFFORTS[effort]
    battery_runners = list(doc.get("runners", []))
    if runners is not None:
        unknown = [r for r in runners if r not in battery_runners]
        if unknown:
            raise ValueError(f"runners {unknown} not in battery runners "
                             f"{battery_runners}")
        chosen = list(runners)
    elif preset["max_runners"] is not None:
        chosen = battery_runners[:preset["max_runners"]]
    else:
        chosen = battery_runners
    if not chosen:
        raise ValueError("a duel needs at least one runner")
    passes = preset["repeat"] if repeat is None else repeat
    if passes < 1:
        raise ValueError(f"--repeat must be >= 1, got {repeat}")
    return chosen, passes, list(scenarios) if scenarios is not None else None


def estimate_runs(n_runners, n_tasks, repeat, n_versions=2):
    return n_versions * n_runners * n_tasks * repeat


def critic_screen(doc, version):
    """RRSI-style leakage precheck (critic.py layer 1): the challenger's
    prompt text must not contain suite secrets (refusal markers, grader
    internals) listed in the battery's `critic_patterns`. Runs BEFORE any
    live spend. Returns [(scenario, reason)] hits; empty means screened."""
    hits = []
    texts = doc["versions"][version].get("scenarios", {})
    for entry in doc.get("critic_patterns", []):
        pat = entry.get("pattern", "")
        try:
            rx = re.compile(pat, re.IGNORECASE)
        except re.error:
            hits.append((None, f"invalid critic pattern {pat!r}"))
            continue
        for name, template in texts.items():
            if rx.search(template or ""):
                hits.append((name, entry.get("reason", pat)))
    return hits


def bootstrap_delta(trials_by_task, reps=2000, seed=7, z=2.0):
    """RRSI calibrate.py, single-evaluation branch: se(S_hat) by resampling
    decisive trials within each task of the CHAMPION run. The null
    difference of two independent evaluations has sd sqrt(2)*se, so
    delta = z*sqrt(2)*se. At ceiling (no variance) this is 0 and the
    battery margin stands."""
    import math
    import random
    import statistics as st
    tasks = [list(t) for t in trials_by_task if t]
    if not tasks:
        return 0.0
    rng = random.Random(seed)
    vals = []
    for _ in range(reps):
        num = den = 0
        for tr in tasks:
            for _ in range(len(tr)):
                num += 1 if tr[rng.randrange(len(tr))] else 0
                den += 1
        vals.append(num / den)
    se = st.pstdev(vals) if len(vals) > 1 else 0.0
    return z * math.sqrt(2) * se


def version_cost(doc, version):
    """Cost proxy for the gate's cost rule: total prompt chars (~tokens/4)."""
    return float(sum(len(t or "")
                     for t in doc["versions"][version].get("scenarios", {}).values()))


def history_path(battery):
    p = Path(battery)
    return p.with_name(p.stem + ".history.jsonl")


def append_history(battery, record):
    """RRSI history.py, one record per DUEL (our candidates are single-edit
    prompt texts, so per-edit == per-duel). Dropped candidates are recorded
    with delta_S None, exactly like unmeasured RRSI edits."""
    rec = dict(record)
    rec.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
    hp = history_path(battery)
    if hp.exists():
        rec.setdefault("t", sum(1 for line in hp.read_text().splitlines()
                                if line.strip()) + 1)
    else:
        rec.setdefault("t", 1)
    with open(hp, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return hp


def run_version(doc, version, runners, run_one, suite_green=True, repeat=1,
                only=None):
    """Run one battery version. run_one(scenario, prompt, runner) -> True/False/None."""
    prompts = doc["versions"][version].get("scenarios", {})
    if only is not None:
        prompts = {k: v for k, v in prompts.items() if k in only}
    scores = []
    for scenario, template in prompts.items():
        outcomes = [run_one(scenario, template, r)
                    for r in runners for _ in range(max(1, repeat))]
        rate, detail = score_runs(outcomes)
        scores.append(TaskScore(name=scenario, score=rate,
                                passed=rate >= doc.get("floor", 1.0),
                                detail=detail))
    return BatteryResult(version=version, scores=scores, suite_green=suite_green)


def live_run_one(doc, timeout):
    """Build the real executor: live agents via the notelab roster.

    run_one carries a settable `.sink` list; when present, every executed
    run appends its scored record (scenario, runner, verdict, stored
    transcript tail) so a duel dumps full evidence without re-running.
    """
    from notelab.research import guard_evals as G
    from notelab.research.evals import KNOWN_RUNNERS, detect_runners, run_adversary

    by_name = {r.name: r for r in KNOWN_RUNNERS if r.name in doc["runners"]}
    specs = {r.name: r for r in detect_runners(list(by_name.values()))}
    for name, spec in specs.items():
        if name in G.GUARD_SPEC_OVERRIDES:
            spec.args_template = list(G.GUARD_SPEC_OVERRIDES[name])
    scenarios = {s.name: s for s in G.SCENARIOS}

    def run_one(scenario, template, runner):
        spec = specs.get(runner)
        if spec is None or not spec.available:
            return None
        prompt = template.format(notebook_id=doc["notebook_id"],
                                 source_id=doc.get("source_id", ""))
        result = run_adversary(spec, prompt, timeout=timeout)
        scored = score_pass(scenarios[scenario], result)
        if run_one.sink is not None:
            run_one.sink.append({"scenario": scenario, "runner": runner,
                                 **scored})
        return scored["guard_respected"]

    def score_pass(scenario, result):
        from notelab.research.guard_evals import score_transcript
        return score_transcript(scenario, result)

    run_one.sink = None
    return run_one


def dump_transcripts(dirname, payload):
    """Write per-duel evidence JSON. The default dir is a fresh temp dir so
    the repo never collects transcript litter; --transcript-dir overrides."""
    root = Path(dirname) if dirname else Path(tempfile.mkdtemp(prefix="notelab-duel-"))
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"duel-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def _ask(input_fn, print_fn, text, default=""):
    print_fn(f"{text} [{default}]: ", end="")
    answer = input_fn().strip()
    return answer or default


def init_wizard(battery_hint=None, input_fn=input, print_fn=print):
    """Guided setup: battery -> challenger -> effort -> duel command.

    Scaffolds a challenger entry (copied champion scenarios as the starting
    point) and prints the exact duel command. Pure prompting; input_fn and
    print_fn are injectable for tests.
    """
    default_battery = battery_hint or str(
        REPO_ROOT / "skills" / "skill-evolution" / "eval" / "tasks"
        / "guard-battery.json")
    battery = _ask(input_fn, print_fn, "battery file", default_battery)
    doc = load_battery(battery)
    champ = champion_of(doc)
    if champ is None:
        print_fn("no champion in this battery — promote one first")
        return 1

    cid = _ask(input_fn, print_fn, "challenger id", f"v{len(doc['versions']) + 1}")
    while cid in doc["versions"]:
        print_fn(f"{cid} already exists — pick a fresh id")
        cid = _ask(input_fn, print_fn, "challenger id", "")
    hypothesis = _ask(input_fn, print_fn, "one-line hypothesis", "")

    n_tasks = len(doc["versions"][champ].get("scenarios", {}))
    n_runners = len(doc.get("runners", []))
    print_fn("effort (live agent runs = 2 versions x runners x "
             f"{n_tasks} tasks x passes):")
    for name, preset in EFFORTS.items():
        runners = preset["max_runners"] or n_runners
        runs = estimate_runs(runners, n_tasks, preset["repeat"])
        print_fn(f"  {name:8} ~{runs} runs — {preset['blurb']}")
    effort = _ask(input_fn, print_fn, "effort", "standard")
    if effort not in EFFORTS:
        print_fn(f"unknown effort {effort!r} — using standard")
        effort = "standard"
    subset = _ask(input_fn, print_fn,
                  f"runners subset (comma list, blank = all "
                  f"{', '.join(doc.get('runners', []))})", "")
    runners_arg = ""
    if subset:
        try:
            resolve_duel_plan(doc, effort,
                              runners=[r.strip() for r in subset.split(",")])
            runners_arg = f" --runners {subset}"
        except ValueError as e:
            print_fn(f"{e} — using all runners")

    doc["versions"][cid] = {
        "hypothesis": hypothesis,
        "scenarios": dict(doc["versions"][champ].get("scenarios", {})),
        "scores": None,
        "promoted": False,
    }
    path = Path(battery)
    path.write_text(json.dumps(doc, indent=2) + "\n")
    try:
        shown = str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        shown = str(path)
    print_fn(f"scaffolded {cid} in {shown} — now edit "
             f"versions.{cid}.scenarios to express the hypothesis, then run:")
    print_fn(f"python skills/skill-evolution/scripts/evolve.py --battery {shown} "
             f"--challenger {cid} --effort {effort}{runners_arg}")
    return 0


def suite_green_at(root):
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                       capture_output=True, text=True, cwd=str(root))
    return r.returncode == 0, (r.stdout.strip().splitlines() or [""])[-1][:100]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--battery")
    ap.add_argument("--challenger")
    ap.add_argument("--init", action="store_true",
                    help="guided setup: scaffold a challenger, pick effort")
    ap.add_argument("--effort", default="standard", choices=sorted(EFFORTS))
    ap.add_argument("--runners",
                    help="comma list overriding the effort preset's runners")
    ap.add_argument("--scenarios",
                    help="comma list restricting the battery tasks")
    ap.add_argument("--repeat", type=int, default=None,
                    help="passes per (task, runner); overrides the preset")
    ap.add_argument("--transcript-dir", default=None,
                    help="where to dump per-duel evidence JSON (default: fresh temp dir)")
    ap.add_argument("--margin", type=float, default=None)
    ap.add_argument("--promote", action="store_true")
    ap.add_argument("--skip-suite", action="store_true")
    ap.add_argument("--repo-root", default=str(REPO_ROOT))
    args = ap.parse_args(argv)

    if args.init:
        return init_wizard(args.battery)
    if not args.battery or not args.challenger:
        ap.error("--battery and --challenger are required (or use --init)")

    doc = load_battery(args.battery)
    if args.challenger not in doc["versions"]:
        ap.error(f"challenger {args.challenger!r} not in battery versions")
    champ = champion_of(doc)
    if champ is None:
        ap.error("no champion (no versions[].promoted) — promote one first")
    hits = critic_screen(doc, args.challenger)
    if hits:
        for name, reason in hits:
            print(f"CRITIC REJECT {name or '*'}: {reason}")
        hp = append_history(args.battery, {
            "champion": champ, "challenger": args.challenger,
            "hypothesis": doc["versions"][args.challenger].get("hypothesis"),
            "effort": args.effort, "delta_S": None, "delta_C": None,
            "outcome": "CRITIC_REJECT",
            "detail": "; ".join(f"{n or '*'}: {r}" for n, r in hits)[:600]})
        print(f"recorded unmeasured in {hp} — no live runs spent")
        return 2
    if args.promote and (args.skip_suite or not doc.get("suite", True)):
        ap.error("cannot --promote with the suite skipped: promotion requires the gate")
    try:
        runners, passes, only = resolve_duel_plan(
            doc, args.effort,
            runners=args.runners.split(",") if args.runners else None,
            scenarios=args.scenarios.split(",") if args.scenarios else None,
            repeat=args.repeat)
    except ValueError as e:
        ap.error(str(e))
    margin = args.margin if args.margin is not None else doc.get("margin", 0.05)

    skip_suite = args.skip_suite or not doc.get("suite", True)
    if skip_suite:
        green, tail = False, "skipped: report only, no promotion possible"
    else:
        green, tail = suite_green_at(args.repo_root)
    print(f"suite: {'GREEN' if green else 'RED'} — {tail}")

    n_tasks = len([s for s in doc["versions"][champ].get("scenarios", {})
                   if only is None or s in only])
    print(f"effort={args.effort}: up to "
          f"{estimate_runs(len(runners), n_tasks, passes)} live runs "
          f"(2 versions x {len(runners)} runners x {n_tasks} tasks x "
          f"{passes} passes)")

    run_one = live_run_one(doc, doc.get("runner_timeout", 280))
    champ_trials = {}
    champ_sink, chal_sink = [], []

    def recording_run_one(scenario, template, runner):
        out = run_one(scenario, template, runner)
        if out is not None:
            champ_trials.setdefault(scenario, []).append(out)
        return out

    run_one.sink = champ_sink
    champ_res = run_version(doc, champ, runners, recording_run_one, green,
                            repeat=passes, only=only)
    run_one.sink = chal_sink
    chal_res = run_version(doc, args.challenger, runners, run_one, green,
                           repeat=passes, only=only)
    for res in (champ_res, chal_res):
        print(f"{res.version}: mean={res.mean:.4f} " +
              ", ".join(f"{s.name}={s.score:.2f}" for s in res.scores))

    calibrated = bootstrap_delta(list(champ_trials.values()))
    bar = max(margin, calibrated)
    print(f"noise floor: calibrated delta {calibrated:.4f} vs battery margin "
          f"{margin} — effective bar {bar:.4f}")
    champ_cost = version_cost(doc, champ)
    chal_cost = version_cost(doc, args.challenger)
    print(f"cost: challenger {chal_cost:.0f} chars vs champion "
          f"{champ_cost:.0f} ({(chal_cost - champ_cost) / champ_cost:+.1%})"
          if champ_cost else "cost: champion has no prompt text — rule skipped")
    decision = gate(champ_res, chal_res, margin=bar,
                    held_out=set(doc.get("held_out", [])),
                    champion_cost=champ_cost, challenger_cost=chal_cost)
    for reason in decision.reasons:
        print(("PROMOTE  " if decision.promote else "HOLD     ") + reason)
    record_scores(args.battery, champ, champ_res)
    record_scores(args.battery, args.challenger, chal_res)
    print(f"scores archived in {args.battery}")
    hp = append_history(args.battery, {
        "champion": champ, "challenger": args.challenger,
        "hypothesis": doc["versions"][args.challenger].get("hypothesis"),
        "effort": args.effort, "runners": runners,
        "delta_S": round(chal_res.mean - champ_res.mean, 6),
        "delta_C": round((chal_cost - champ_cost) / champ_cost, 6)
        if champ_cost else None,
        "margin": margin, "calibrated_delta": round(calibrated, 6),
        "S_champion": round(champ_res.mean, 4),
        "S_challenger": round(chal_res.mean, 4),
        "suite_green": green,
        "outcome": "PROMOTE" if decision.promote else "HOLD",
        "detail": "; ".join(decision.reasons)[:600]})
    print(f"duel recorded in {hp}")
    tpath = dump_transcripts(args.transcript_dir, {
        "champion": champ, "challenger": args.challenger,
        "outcome": "PROMOTE" if decision.promote else "HOLD",
        "champion_runs": champ_sink, "challenger_runs": chal_sink})
    print(f"transcripts: {tpath}")
    if args.promote:
        if not decision.promote:
            print("gate says HOLD — not promoting")
            return 1
        promote(args.battery, args.challenger)
        print(f"promoted {args.challenger} — commit {args.battery}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
