---
name: skill-evolution
description: "Champion/challenger evolution for self-improving skills: evaluate prompt variants through a fixed battery, gate promotion on margin + held-out + green tree, archive scores in git. Use when a skill (or any prompt text: eval scenarios, spar prompts, agent instructions) should get measurably better over versions instead of edited on vibes. Triggers on: evolve skill, improve prompt, champion challenger, prompt A/B, regression gate, held-out eval, promote version."
version: "1.0.0"
---

# Skill Evolution (champion/challenger)

Evals are the control plane; prompts are disposable variants. This skill turns
"edit and hope" into a measured loop:

```
variant -> eval battery -> score -> gate -> promote/archive -> repeat
```

## The protocol

1. **Fix the battery first.** A battery is a JSON file listing tasks + metric +
   floor + held-out split (see `eval/tasks/guard-battery.json`). The battery
   NEVER changes mid-duel — comparable scores or it didn't happen.
2. **Champion vs challenger.** The champion is the newest `promoted: true`
   version in the battery file. A challenger is a prompt-text edit proposed by
   an agent or human. One dimension per duel (wording, structure, examples —
   never all three).
3. **Run both, same conditions.** `scripts/evolve.py --battery <file>
   --challenger <v>` runs champion + challenger through every task. Live tasks
   need real runners (the installed agent-CLI families via notelab's
   `run_adversary` roster); the `suite` task always runs as the regression
   gate. Before any
   spend, a critic screen rejects challengers whose text contains suite
   secrets (battery `critic_patterns`) — leakage never gets a measurement.
4. **Gate, don't vibe.** Promote iff ALL hold (see
   `src/notelab/research/skill_evolve.py::gate`):
   - challenger mean beats the effective bar: `max(margin, calibrated
     noise floor)` — the floor is bootstrapped from the champion's own
     trial variance, so noisy small batteries demand bigger wins;
   - relative prompt growth fits the cost budget `0.5 + 4 * gain` —
     a doubling needs a +0.125 gain; cheap wins always pass;
   - no held-out task regresses (this is the overfitting guard — a tuned
     prompt that only wins held-in tasks is memorizing the battery);
   - the tree is green on both runs.
5. **Archive in git.** Scores are written back into the battery JSON, one
   record per duel goes to `<battery>.history.jsonl`; promote with
   `--promote` (stamps the flag, exactly one champion). Commit. Losers
   stay inspectable — never delete a version, only unseat it.

## Effort dial

Match spend to stakes — every duel prints its live-run budget up front
(`2 versions x runners x tasks x passes`; on the 3-task guard battery
that's 6 / 12 / 24 runs):

- `smoke` — first battery runner only. Cheap signal for "is this idea
  alive / did I break the harness".
- `standard` — all runners, one pass. The default duel.
- `deep` — all runners, two passes. For close calls where one flaky run
  could flip the verdict.

`scripts/evolve.py --battery <f> --challenger <v> --effort smoke`, with
`--runners`, `--scenarios`, `--repeat` overriding the preset piecemeal.
Battery order is runner-preference order: put the cheapest reliable
runner first so smoke spends wisely.

## Guided setup

New here? `scripts/evolve.py --init` walks through battery →
challenger id → hypothesis → effort (with live-run estimates) →
optional runner subset, scaffolds the challenger entry from the
champion's scenarios, and prints the exact duel command. Edit the
scaffolded scenarios to express the hypothesis, then run the command.

## Rules

- **Held-out is sacred.** Never tune prompts against held-out tasks, never
  move a failing task from held-in to held-out (or reverse) to flip a verdict.
- **Battery edits reset the duel.** Changed a task, floor, or runner? Both
  versions re-run from scratch. Mixed-condition scores are void.
- **Cap prompt growth.** Evolved prompts bloat (~2KB → 68KB observed in the
  literature). The gate's cost rule enforces this as math, not advice —
  see protocol step 4.
- **Score honestly.** Env failures (quota, auth, no shell) are no-signal,
  never pass/fail. A bare echo of the prompt is not a pass. See the
  research-loop cookbook's guard flow.
- **One duel at a time per battery.** Parallel challengers race each other
  into noise; queue them.
- **`--init` writes into the battery file you point it at.** That is by design
  (it scaffolds the challenger entry for you to edit). Run it against *your own*
  battery, not the shipped `eval/tasks/guard-battery.json` example — copy that
  first if you want a starting point, so the tracked example stays clean.

## Runners and judges

notelab owns the battery + gate; the *runners and judges* come from whatever
agent CLIs you have installed: multi-family agent CLIs execute live tasks, and
(optionally) an external orchestrator can propose challenger variants. Ledger
note: record each duel as a notelab experiment (arms = champion/challenger) so
`research_history` shows the skill's lineage.

## References

- Pattern sources: `references/evolution-pattern.md` (what we adopted from
  the harness literature, and what we deliberately didn't).
- Worked example: `eval/tasks/guard-battery.json` — the guard-scenario
  prompts as evolvable versions, ready to duel.
