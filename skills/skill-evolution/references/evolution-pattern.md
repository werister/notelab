# What we adopted (and didn't) from the harness literature

Surveyed October 2026: Agent Skills standard (agentskills.io, Anthropic),
agentic-eval-evolution-runtime (eval-as-control-plane thesis, Mode A/B),
ultragent (evolutionary loop on Claude Code config), microsoft-agentic-harness
(Meta-Harness Optimizer on skill files), harness-evolution notes (overfitting
in harness space), Genex (snapshot + rollback in git), RRSI
(google-research/rrsi — regularized harness search, the deepest mechanism
source below).

## Adopted

- **Variant → eval → gate → promote/archive.** The convergent loop shape.
  Our archive is the battery JSON in git (scores versioned with the prompts
  they judge) instead of a separate store.
- **Evals are the control plane.** Runners (model families) are interchangeable;
  the battery + gate are the durable asset. notelab's `run_adversary` roster
  already gives us 11 swappable runners.
- **Held-in / held-out split.** The single most important anti-overfitting
  control. A challenger that only wins held-in tasks is memorizing the battery.
- **Regression gate on the tree.** Never promote on red — the loop must not
  launder harness breakage into "prompt improvements".
- **Git as snapshot/rollback.** Losers stay inspectable; promotion is a commit.
  No separate version store to drift.
- **RRSI critic precheck.** The battery's `critic_patterns` regex denylist
  (refusal markers, grader internals) screens the challenger BEFORE any
  live spend; hits are recorded unmeasured, exactly like RRSI's dropped
  candidates. (`scripts/evolve.py::critic_screen`)
- **RRSI cost rule.** Above the margin band, relative prompt growth must
  fit `beta0 + beta1 * gain` (defaults 0.5/4.0 — a doubling needs a
  +0.125 gain). This formalizes the old prose growth-cap into gate math.
  (`skill_evolve.py::cost_rule`)
- **RRSI calibrated noise floor.** The effective bar is
  `max(battery margin, bootstrapped delta)` where delta comes from
  resampling the champion's decisive trials (`z*sqrt(2)*se`, RRSI's
  single-evaluation branch). At ceiling the battery margin stands; on
  noisy small samples the bar rises honestly instead of pretending a
  0.05 gap is signal.
- **RRSI duel history.** Every duel appends one JSONL record beside the
  battery (`<stem>.history.jsonl`): champion/challenger, hypothesis,
  delta_S/delta_C, verdict + reasons. Our candidates are single-edit
  prompt texts, so per-edit == per-duel — no bundle attribution needed.
- **RRSI guards + edit budget, already ours.** Suite-green and held-out
  no-regression ARE non-compensatory domain guards; "one dimension per
  duel" IS an edit budget of 1, permanently annealed. RRSI gave us the
  vocabulary; the rules predate it.

## Deliberately not adopted

- **Auto-mutation of prompts.** Several harnesses let the loop rewrite prompts
  itself. We keep mutation human/agent-proposed and gate automatic: the gate
  is trustworthy, the mutator is not yet. Revisit when batteries are bigger.
- **Learned routing over a harness tree.** Premature at our scale — one
  champion per battery, queued duels. The literature's overfitting warnings
  (prompt bloat, early-stream capture) are why the protocol caps growth and
  queues duels instead.
- **Separate eval dataset store.** Batteries live next to the skill
  (`eval/tasks/*.json`) so the thing being judged and its judge travel
  together. Graduate to a store if batteries outgrow files.
- **RRSI LLM critic review.** Layer 2 (model-judged leakage/intent review)
  costs a reviewer call per duel and needs a reviewer model on tap. The
  regex layer catches the mechanical cheats (marker echo, grader reads);
  revisit as a `deep`-effort-only step when challengers get subtler.
- **RRSI novelty/prune machinery.** Meaningful when the harness has many
  structural components competing for slots. A prompt battery has one
  component (the text) — there is nothing to prune toward. Revisit if
  batteries ever evolve multi-part harnesses (prompts + tools + memory).
- **RRSI shaped within-band accept.** RRSI can promote inside the noise
  band via `w_s*dS - w_c*dC + w_n*nu > 0`. Without a novelty term that
  reduces to "cheaper ties win", which we don't want to bless silently —
  our margin rule holds the whole band. A cheap tie is a HOLD with an
  invitation to re-duel at `deep`.
- **RRSI git-worktree candidates.** Their candidates are harness
  directories; ours are JSON prompt texts — the battery file in git is
  already the worktree, the incumbent commit, and the archive.
