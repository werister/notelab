"""
laya_judge — Laya as an ablation tie-breaker (fixes FIELD-NOTES.md gap #8).

Gated, not default: `ablation.py` only imports this module's names, never
`laya_mlx` itself at module load time. A caller opts in explicitly via
`ablate_sources(..., use_laya=True)` — the stdlib-only, no-network default path
(`use_laya=False`, the default) is completely unaffected by this module existing,
same guarantee `research/semantic.py` makes for its Mistral backend.

Unlike semantic.py's hosted API call, this downloads and EXECUTES a third-party
model checkpoint from the Hugging Face Hub -- a different risk class, so the
revision is pinned to an exact commit, not a floating tag.

Laya (Convai Innovations, https://huggingface.co/convaiinnovations/laya) is a
421M-parameter, non-autoregressive decision model: given a `state` dict and typed
`questions` (choice/score/noul), it returns calibrated probabilities in a single
forward pass -- no text generation. Verified live: correctly picked the more
relevant of two real sources (80.6% probability) on a real research question.
"""
from __future__ import annotations

from dataclasses import dataclass

# Pinned to the exact commit fetched and verified live this session -- a checkpoint
# update upstream must not silently change this tie-breaker's behavior underneath a
# pinned notelab release.
_MODEL_ID = "convaiinnovations/laya"
_PINNED_REVISION = "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851"

_agent = None  # module-level singleton: the ~36s checkpoint load is one-time per
                # process (assumes a long-lived MCP server process -- see the spec's
                # "Realistic cost" note if that assumption doesn't hold), not per-call.


class LayaJudgeError(RuntimeError):
    """laya-mlx isn't installed, or the model call itself failed. Never a silent
    no-op fallback to the old (wrong) default -- same principle SemanticExpansionError
    already establishes: a caller who asked for a real judgment and got none must be
    told, not quietly downgraded."""


@dataclass
class RedundancyJudgment:
    verdict: str  # "redundant" | "load-bearing" -- one of the existing constants
    confidence: float
    probabilities: dict[str, float]


def laya_available() -> bool:
    try:
        import laya_mlx  # noqa: F401
    except ImportError:
        return False
    return True


def _get_agent():
    global _agent
    if _agent is None:
        if not laya_available():
            raise LayaJudgeError(
                "laya-mlx is not installed — pip install 'notelab[laya]'")
        import laya_mlx
        try:
            _agent = laya_mlx.load(_MODEL_ID, revision=_PINNED_REVISION)
        except Exception as e:
            raise LayaJudgeError(f"failed to load Laya checkpoint: {e}") from e
    return _agent


def judge_redundancy(source, remaining: list, question: str) -> RedundancyJudgment:
    """`remaining` is intentionally unused by the current scoring question (kept in
    the signature to match ablate_sources' call site and for a future comparative
    framing) — verified live that comparing source against a digest of `remaining`
    produced a near-coin-flip regardless of digest content; scoring `source` on its
    own specificity, independent of the rest, is what actually worked.

    Ask Laya: does `source` carry specific, checkable value for `question`, or
    is it generic restatement the remaining set already covers? Only called by
    ablate_sources for the ambiguous zone the fixed-epsilon threshold gets wrong
    (a fixed composite_eps vs a per-source mean that shrinks as ~1/N — see
    FIELD-NOTES.md gap #8).

    Uses a `score` question (specificity, 0-3), not a direct `choice` between
    "redundant"/"load_bearing" — verified live this session that the direct
    binary choice produces a near-coin-flip (~52/48, confidence <0.02) regardless
    of how differentiated the two sources actually are, while scoring each
    source's own specificity independently gives a clean, confident signal
    (a source with concrete names/numbers/policy details scored ~2.0 "mostly
    specific"; generic keyword-restatement filler scored ~0.6 "vague" — same
    checkpoint, same fixture, only the question shape changed).

    Availability is checked inside _get_agent(), not here. A duplicate check at
    this level breaks offline test mocking: tests patch _get_agent directly to
    inject a fake agent, but a redundant laya_available() check here would run
    first regardless of that patch and raise whenever laya-mlx isn't installed
    (as it isn't in CI), defeating the mock."""
    agent = _get_agent()

    state = {
        "question": question,
        "passage": (source.content or "")[:800],
    }
    questions = {
        "specificity": {
            "type": "score",
            "instructions": (
                "How much specific, concrete, checkable information (names, "
                "numbers, policy/technical details) does passage contain about "
                "question, versus vague generic restatement of the topic?"
            ),
            "criteria": [
                "vague generic restatement only",
                "some specifics",
                "mostly specific",
                "highly specific, concrete facts",
            ],
        },
    }
    try:
        result = agent.predict(state, questions)
    except Exception as e:
        raise LayaJudgeError(f"Laya prediction failed: {e}") from e

    answer = result["answers"]["specificity"]
    # legend: 0/1 = vague/some specifics -> redundant (generic restatement is
    # exactly what "the remaining sources already cover" looks like); 2/3 =
    # mostly/highly specific -> load-bearing. Midpoint of the 0-3 scale.
    verdict = "load-bearing" if answer["score"] >= 1.5 else "redundant"
    return RedundancyJudgment(
        verdict=verdict,
        confidence=answer["confidence"],
        probabilities=answer["probabilities"],
    )
