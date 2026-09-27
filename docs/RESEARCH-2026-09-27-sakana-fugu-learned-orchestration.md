# Research: Sakana Fugu — learned multi-agent orchestration, and what's transferable

Grounded directly in the primary source — Sakana AI's technical report (arXiv:2606.21228,
"Sakana Fugu Technical Report," Fugu Team, 2026-06-24), read in full via its PDF, not
summarized from secondary coverage — plus real, live-checked commercial pricing from
OpenRouter for the parts the paper itself doesn't cover (API economics). Written because the
user pointed at Fugu as "the no-cost-restrictions version of what we're trying to do" with
the free-tier swarm dispatcher this session built, and asked for real research through
notelab specifically, not a shallow summary.

## What Fugu actually is

Two released variants, at different points on a quality/latency tradeoff — this distinction
matters directly for our own design, see "Transferable ideas" below:

**Fugu** (the fast variant) builds on prior work called *Trinity* (Xu et al., 2025 — cited,
not independently verified here, Fugu's own paper is the primary source for how Trinity's
idea gets used). Its orchestrator is **not a text-generating model for the routing decision
at all**: a lightweight prediction head sits after the orchestrator backbone's final hidden
layer and outputs L logits — one per worker model in the pool — directly from a hidden state.
The routing decision is a single forward pass over hidden states, never an autoregressive
generation step, which the paper states explicitly is "central to Fugu's latency profile" and
what "makes inference substantially cheaper." Unlike Trinity, Fugu **never assigns a role** to
the selected worker — it only ever picks one single worker per query and dispatches to it
as-is. The paper is explicit that removing role assignment is a deliberate simplification:
"narrows the coordination space and minimizes the latency overhead of the orchestrator." Fugu
also uses singular-value fine-tuning (decomposing selected weight matrices, training only the
singular-value scales, keeping the orthogonal components frozen) — an efficient-adaptation
technique, not full fine-tuning, on top of a pretrained backbone.

**Fugu-Ultra** (the quality-first variant) is a different mechanism entirely, built on a
separate framework the paper calls *the Conductor* (Nielsen et al., 2025 — same caveat, cited
not independently verified). Here the orchestrator genuinely **is** a text-generating model:
it outputs a full "agentic workflow" as structured natural language — a sequence of steps,
each specifying a natural-language subtask description, an integer id naming which worker
agent executes it, and an "access list" indexing which prior steps' outputs that worker gets
as context. This representation is expressive enough to encode sequential chains, parallel
best-of-N, or arbitrary tree-structured topologies purely through what the orchestrator
writes — the paper states training produces "problem decompositions... alongside
communication topologies that leverage both independent work and agent-to-agent
collaboration according to the task requirements," i.e., the topology itself (chain vs.
council vs. tree) is a *learned, per-query choice*, not a fixed pattern picked by a human in
advance.

## Training — two genuinely different approaches, per variant

**Fugu (Trinity-based):** two stages.
1. *Supervised fine-tuning on single-step tasks.* For each question in a large curated set
   (spanning coding, math, reasoning, language, agentic scenarios — each with a verifiable
   ground-truth solution), every worker model in the pool is run `n` times and scored against
   the ground truth. Critically, the paper does **not** collapse this to "train on the single
   best worker" — the average reward per worker gets converted into a *soft* target
   distribution via a temperature-scaled softmax (their eq. 1), and the head is trained to
   minimize KL divergence against that full distribution (eq. 2), not a hard classification
   label. Their stated reason: "gives the orchestrator a richer training signal and makes its
   selection more robust when several workers are similarly capable" — a real, technically
   sound justification, not just an incidental detail.
2. *Evolutionary optimization on end-to-end tasks* (sep-CMA-ES, "following the optimization
   approach of Trinity"). The single-step SFT data doesn't capture real multi-turn agentic
   behavior, so this stage collects **real interaction trajectories from actual production
   coding harnesses — explicitly named: Claude Code, Codex, and OpenCode** — and directly
   maximizes expected terminal task-completion reward (their eq. 3) via evolution strategies
   over the orchestrator's parameters (not gradient descent — the paper's stated reason is
   that ES handles sparse/binary terminal rewards over multi-turn trajectories without needing
   dense per-step labels, and is empirically "substantially more stable" here than continuing
   supervised fine-tuning on the same data would be).

**Fugu-Ultra (Conductor-based):** trained with GRPO (Shao et al., 2024 — a real, established
RL algorithm, group-relative policy optimization; eq. 6-7 in the paper). The reward shaping is
explicit and two-tiered: **0** if the generated workflow can't even be parsed into a valid
subtask/worker/access-list sequence (a hard format gate), **0.5** if it parses but the
resulting execution doesn't match the ground-truth solution, **1.0** if it parses AND the
final answer is correct. This reward structure is directly relevant to our own escalation
design — see below.

## Real numbers, and an honest split of what's primary-source-verified vs. not

- **Benchmarks (primary source, verified from the paper's own Figure 1):** Fugu and
  Fugu-Ultra are shown beating or matching named frontier baselines (the chart includes
  Claude, GPT, Gemini-family bars, generically labeled by the paper as comparison points) on
  Terminal Bench 2.1, CharXiv Reasoning, GPQA-Diamond, LiveCodeBench, SciCode, SWE-Bench Pro,
  Humanity's Last Exam (text), and CTF-REALM. Exact numeric scores are readable in the chart
  images but not transcribed here as precise figures — treat "Fugu is competitive with or
  beats frontier baselines on several hard benchmarks" as the verified claim, not any specific
  percentage, since bar-chart values weren't independently cross-checked against the paper's
  results tables.
- **Commercial pricing (secondary source — OpenRouter's own listing, not the paper, which is
  a research report and doesn't discuss API economics):** Fugu-Ultra: $5.00/M input tokens,
  $30.00/M output tokens, $0.50/M cache-read, $10.00/1K web-search calls. Fugu (the "Max"
  commercial tier, per AlphaSignal's reporting — not independently re-verified against
  OpenRouter directly in this pass): $2/M input, $6/M output, reported as 40-60% below
  Sonnet 5 / GPT 5.6 Terra list pricing. The ~1,260-token fixed orchestration overhead and the
  5-12x total-token multiplier on complex queries (also from AlphaSignal/Requesty's reverse-
  engineering coverage) are **not** verified against the primary paper or a live API response
  in this pass — flagged explicitly as unverified secondary claims, not facts, per this
  repo's own standing rule against blurring "verified live" and "estimated."

## Transferable ideas for the free-tier swarm — and what genuinely isn't transferable

**Directly transferable, cost-free to adopt:**
1. **Soft-distribution supervision beats hard-label routing**, even without training anything
   — when our own circuit-breaker/roster logic picks which lane implements a task, treating
   "which model is best for this task shape" as a *ranked preference learned from outcomes*
   (this session's real data: kilo's careful, well-tested diffs vs. opencode's
   `nemotron-3.5-lightning-free` garbling under load) rather than a single hard-coded default
   is the same insight Fugu's SFT stage encodes formally. We don't need a trained head to
   benefit from the idea — a simple per-lane reliability score, updated from real
   `AgentOutcome` history in the ledger, approximates it cheaply.
2. **The parse-then-correctness reward shape (0 / 0.5 / 1.0) is a genuinely useful escalation
   signal**, not just a training-time detail. Our own chain/council designs can classify each
   hop's outcome the same three ways — "didn't produce anything usable" (0), "produced
   something structurally valid but wrong" (0.5), "passed" (1.0) — and use the *middle* bucket
   specifically as the trigger for "needs a critique/refine round," rather than only
   distinguishing pass/fail.
3. **Fugu deliberately trained on real trajectories from Claude Code, Codex, and OpenCode
   specifically** (not synthetic benchmarks) for its evolutionary stage — direct validation of
   this session's own approach of testing against a REAL task (gap #2) with REAL CLI tools
   rather than a synthetic eval, and a concrete argument for continuing to log every real
   swarm run into the ledger as future training-quality data, even before anyone trains
   anything on it.

**Explicitly NOT transferable, and why:**
1. **Fugu's core latency trick — a single forward pass over logits, no generation — requires
   owning/training the orchestrator's weights.** Every "decision" in our swarm (which lane
   implements, whether a critique found real issues, whether to escalate) has to go through an
   actual CLI call to an actual model, at that model's actual latency and (for non-free tiers)
   cost. We cannot get Fugu's "orchestration is nearly free" property without training our own
   small router — explicitly out of scope per the loop-orchestration spec's own Non-goals.
2. **Fugu-Ultra's free-form natural-language workflow generation assumes an unconstrained
   worker pool it can freely call per-query, billed accordingly.** Our worker pool is
   constrained by real, observed account limits (cline's Credits balance, kiro-cli's monthly
   cap) that a trained conductor with Sakana's own economics never had to model. Any
   escalation/routing logic we build has to treat "this lane is unavailable this session" as a
   hard constraint the decision must respect, not a cost the orchestrator can optimize around
   the way Fugu-Ultra's RL reward does.
3. **Fugu's benchmark wins are measured against expensive frontier models it's *replacing*,
   not against free-tier CLI agents it's *racing*.** The transferable shape (learned routing
   beats a single fixed model) doesn't imply our free-tier roster will ever match frontier
   quality — this session's own data (opencode's default model hallucinating a garbled shell
   command) is a real, concrete reminder that "cheap and orchestrated" is not the same claim
   as "as good as Sonnet," and our escalation-to-Claude path exists precisely because that gap
   is real, not hypothetical.

## What this doc does not do

Doesn't propose training an actual router (explicitly out of scope for the loop-orchestration
spec this research feeds). Doesn't re-verify the AlphaSignal/Requesty cost-structure claims
against a live API call — flagged as open, not resolved, if that precision is needed later.
