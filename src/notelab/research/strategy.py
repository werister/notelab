"""
M3 — strategy memory.

M1 gave a stub (`suggest_next_prompt` = reuse the last winner). M3 makes the memory
actually *learn*: it turns a topic's experiment history into (a) a set of prompt
strategies to try next — the proven winner PLUS mutations that explore around it —
and (b) a tuned weight profile for that topic, nudged toward the signals that the
winning sources actually scored high on.

Pure and deterministic (mutation is rule-based, not LLM) so it is reproducible and
testable offline. The caller persists the profile in the ledger and feeds the
suggested strategies straight into the next A/B run.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .ab import Strategy
from .quality import QualityWeights

# Rule-based prompt mutators — cheap ways to explore around a winning prompt without
# an LLM. Each takes the winner text and returns a variant with a distinct emphasis.
_MUTATORS: list[tuple[str, str]] = [
    ("authority", "{p} — prioritise primary sources, peer-reviewed papers, and official docs"),
    ("recency", "{p} — focus on the most recent developments and sources from the last 12 months"),
    ("breadth", "{p} — cover a broad range of independent sources and opposing viewpoints"),
    ("depth", "{p} — go deep into technical detail and mechanisms, not summaries"),
]


@dataclass
class TopicProfile:
    """Per-topic learned state, persisted in the ledger."""
    topic: str
    best_prompt: str | None = None
    best_score: float = 0.0
    weights: dict[str, float] = field(default_factory=dict)
    runs: int = 0

    def as_dict(self) -> dict:
        return {"topic": self.topic, "best_prompt": self.best_prompt,
                "best_score": round(self.best_score, 4), "weights": self.weights,
                "runs": self.runs}


def tune_weights_from_winner(signal_means: dict[str, float],
                             base: QualityWeights | None = None,
                             learning_rate: float = 0.3) -> QualityWeights:
    """Nudge weights toward the signals the winning run scored HIGH on.

    Intuition: if the best sources for this topic were strong on authority + recency,
    weight those more next time. The nudge is bounded by learning_rate so one run can't
    swing the profile wildly; weights are renormalized.
    """
    b = (base or QualityWeights()).normalized()
    keys = ["authority", "diversity", "recency", "depth", "relevance"]
    cur = {k: getattr(b, k) for k in keys}
    total_signal = sum(max(0.0, signal_means.get(k, 0.0)) for k in keys) or 1.0
    tuned = {}
    for k in keys:
        target = max(0.0, signal_means.get(k, 0.0)) / total_signal  # signal's share
        tuned[k] = (1 - learning_rate) * cur[k] + learning_rate * target
    s = sum(tuned.values()) or 1.0
    tuned = {k: v / s for k, v in tuned.items()}
    return QualityWeights(**tuned, recency_halflife_days=b.recency_halflife_days,
                          depth_target_chars=b.depth_target_chars,
                          authority_overrides=b.authority_overrides)


def suggest_strategies(topic: str, best_prompt: str | None,
                       explore: int = 2) -> list[Strategy]:
    """Build the next A/B arm set: exploit the winner + `explore` mutated variants.

    With no history, returns a single neutral baseline arm so the caller can bootstrap.
    """
    if not best_prompt:
        return [Strategy(name="baseline", prompt=f"Research: {topic}")]
    arms = [Strategy(name="winner", prompt=best_prompt)]
    for label, template in _MUTATORS[:max(0, explore)]:
        arms.append(Strategy(name=f"explore_{label}", prompt=template.format(p=best_prompt)))
    return arms


def profile_from_history(topic: str, best_prompt: str | None, best_score: float,
                         winner_signal_means: dict[str, float] | None,
                         runs: int) -> TopicProfile:
    """Assemble a persistable TopicProfile from a topic's experiment history."""
    weights = {}
    if winner_signal_means:
        w = tune_weights_from_winner(winner_signal_means)
        weights = {"authority": round(w.authority, 4), "diversity": round(w.diversity, 4),
                   "recency": round(w.recency, 4), "depth": round(w.depth, 4),
                   "relevance": round(w.relevance, 4)}
    return TopicProfile(topic=topic, best_prompt=best_prompt, best_score=best_score,
                        weights=weights, runs=runs)
