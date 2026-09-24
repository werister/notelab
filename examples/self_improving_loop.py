#!/usr/bin/env python3
"""
Offline walkthrough of the whole self-improving research loop — NO network, NO
NotebookLM account. It uses a fake research runner with fixture source sets so you
can see exactly what the engine does before you point it at your real NotebookLM.

Run:
    python examples/self_improving_loop.py

It demonstrates, in order:
  1. A/B — two prompt strategies scored on the DATA QUALITY of the sources each pulls.
  2. Ledger — recording the experiment and reusing the winning prompt next time.
  3. Evolve — a create/merge/skip/evict plan that curates the notebook's sources.
  4. Evals — deciding whether a strategy is improving or regressing over time.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from notelab.research.ab import Strategy, run_ab, suggest_next_prompt  # noqa: E402
from notelab.research.evals import eval_skill_trend  # noqa: E402
from notelab.research.evolve import EvolutionPolicy, plan_evolution  # noqa: E402
from notelab.research.quality import SourceFeatures  # noqa: E402
from notelab.storage import Store  # noqa: E402

NOW = datetime.now(timezone.utc)


def src(id, url, content, days=30):
    return SourceFeatures(id=id, url=url, content=content,
                          published=NOW - timedelta(days=days))


def fake_runner(strategy: Strategy):
    """Pretends to run NotebookLM Deep Research. The 'authoritative' prompt pulls a
    better source set (primary/gov/papers, fresh, deep); the broad one pulls a farm."""
    if "authoritative" in strategy.prompt:
        srcs = [
            src("s1", "https://arxiv.org/abs/2401", "quantum error correction surface codes " * 120),
            src("s2", "https://www.nih.gov/study", "decoherence fault tolerance thresholds " * 120, days=60),
            src("s3", "https://nature.com/article", "logical qubits stabilizer measurement " * 120, days=90),
        ]
    else:
        srcs = [
            src("s4", "https://top10-best-quantum.com/a", "buy quantum now", days=2500),
            src("s5", "https://top10-best-quantum.com/b", "cheap qubits click here", days=2500),
        ]
    return srcs, {"notebook_id": f"nb-{strategy.name}", "task_id": f"t-{strategy.name}"}


def rule(title):
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def main():
    question = "quantum error correction fault tolerant thresholds"

    rule("1. A/B — score prompt strategies on SOURCE data quality")
    exp = run_ab(question, [
        Strategy(name="broad", prompt="tell me about quantum computing"),
        Strategy(name="authoritative", prompt="find authoritative primary quantum sources"),
    ], runner=fake_runner)
    result = exp.as_dict()
    print(f"winner: {result['winner']}  (score {result['winner_score']}, "
          f"margin {result['margin_over_runner_up']})")
    for row in result["ranking"]:
        print(f"  {row['name']:<15} quality={row['quality_score']:<7} "
              f"n_sources={row['n_sources']}")

    rule("2. Ledger — record it, then reuse the winning prompt for a repeat question")
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "demo.db")
        weights = result["arms"][0]["report"]["weights"]
        exp_id = store.record_experiment(result, weights)
        print(f"recorded experiment #{exp_id}")
        winners = store.prior_winners(question)
        print(f"suggested prompt next time: {suggest_next_prompt(winners)!r}")
        store.close()

    rule("3. Evolve — curate the notebook's sources (create/merge/skip/evict)")
    existing = [
        src("keep1", "https://arxiv.org/abs/1", "quantum error correction " * 120),
        src("dupe", "https://arxiv.org/abs/1", "quantum error correction " * 120),  # dup of keep1
        src("stale", "https://top10-best-quantum.com/x", "buy now", days=3000),     # weak
    ]
    candidates = [
        src("new_good", "https://www.nist.gov/pub", "fault tolerance thresholds " * 120, days=10),
        src("new_weak", "https://top10-best-quantum.com/y", "cheap", days=3000),
    ]
    pol = EvolutionPolicy(quality_floor=0.35, evict_after_strikes=2)
    stats = {}
    for cycle in (1, 2):
        # cands only enter on cycle 1; cycle 2 shows the stale source accruing strikes
        cands = candidates if cycle == 1 else []
        plan, stats = plan_evolution("nb-demo", existing=existing, candidates=cands,
                                     stats=stats, question=question, policy=pol)
        print(f"\n-- cycle {cycle} -- {json.dumps(plan.as_dict()['summary'])}")
        for item in plan.as_dict()["items"]:
            extra = f" -> {item.get('merges_with')}" if item.get("merges_with") else ""
            print(f"  {item['action']:<7} {item['source_id']:<10} "
                  f"score={item['score']:<7}{extra}  # {item['reason']}")

    rule("4. Evals — is the 'authoritative' strategy improving or regressing?")
    # simulate a score history accruing in the ledger over several runs
    history = [0.34, 0.41, 0.49, 0.58, 0.66]
    ev = eval_skill_trend("authoritative", history)
    print(f"trend: {ev.trend.value}  ({ev.verdict})")
    print(f"scores: {ev.as_dict()['scores']}")


if __name__ == "__main__":
    main()
