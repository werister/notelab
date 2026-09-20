"""
Prompt A/B for deep research.

Given a research question and several *strategies* (a prompt variant + knobs like
research mode and source type), this compares them on the deterministic source-quality
signal from `quality.py` and ranks them. It never returns a bare "B won" — it returns
every strategy's decomposed score so the ranking is auditable.

The actual research execution is injected as a callable (`runner`), so this module is
unit-testable with a fake runner and never needs the network. In production the runner
is the nlmx adapter (start -> poll -> import into an isolated notebook, then list sources).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from .quality import QualityReport, QualityWeights, SourceFeatures, score_sources


@dataclass
class Strategy:
    """One arm of the experiment: how to ask NotebookLM to research the question."""
    name: str
    prompt: str
    mode: str = "deep"          # "fast" | "deep"
    source: str = "web"         # "web" | "drive"
    notebook_id: str | None = None  # isolated destination; adapter creates one if None

    def as_dict(self) -> dict:
        return {"name": self.name, "prompt": self.prompt, "mode": self.mode,
                "source": self.source, "notebook_id": self.notebook_id}


@dataclass
class ArmResult:
    strategy: Strategy
    sources: list[SourceFeatures]
    report: QualityReport
    notebook_id: str | None = None
    task_id: str | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy.as_dict(),
            "notebook_id": self.notebook_id,
            "task_id": self.task_id,
            "error": self.error,
            "report": self.report.as_dict() if self.report else None,
        }


class ResearchRunner(Protocol):
    """Runs one strategy end to end and returns the sources it pulled in.

    Implementations: `nlmx_runner` (real) and any fake in tests. Must be side-effect
    honest — an isolated notebook per arm so arms don't cross-contaminate.
    """
    def __call__(self, strategy: Strategy) -> tuple[list[SourceFeatures], dict]:
        """Return (sources, meta). meta may carry notebook_id/task_id/error."""
        ...


@dataclass
class Experiment:
    question: str
    arms: list[ArmResult] = field(default_factory=list)
    weights: QualityWeights = field(default_factory=QualityWeights)

    @property
    def ranking(self) -> list[ArmResult]:
        scored = [a for a in self.arms if a.report and a.error is None]
        return sorted(scored, key=lambda a: a.report.quality_score, reverse=True)

    @property
    def winner(self) -> ArmResult | None:
        r = self.ranking
        return r[0] if r else None

    def as_dict(self) -> dict:
        win = self.winner
        rank = self.ranking
        margin = None
        if len(rank) >= 2:
            margin = round(rank[0].report.quality_score - rank[1].report.quality_score, 4)
        return {
            "question": self.question,
            "n_arms": len(self.arms),
            "winner": win.strategy.name if win else None,
            "winner_score": round(win.report.quality_score, 4) if win else None,
            "margin_over_runner_up": margin,
            "ranking": [
                {"name": a.strategy.name,
                 "quality_score": round(a.report.quality_score, 4),
                 "n_sources": a.report.n_sources}
                for a in rank
            ],
            "arms": [a.as_dict() for a in self.arms],
        }


def run_ab(
    question: str,
    strategies: list[Strategy],
    runner: ResearchRunner,
    weights: QualityWeights | None = None,
) -> Experiment:
    """Execute every strategy, score each arm's sources, return a ranked Experiment.

    Arms run sequentially on purpose: NotebookLM Deep Research is rate/quota limited
    and the host this ships on is often memory-tight. Parallelism is a v2 opt-in.
    """
    w = weights or QualityWeights()
    exp = Experiment(question=question, weights=w)
    for strat in strategies:
        try:
            sources, meta = runner(strat)
            report = score_sources(sources, question=question, weights=w)
            exp.arms.append(ArmResult(
                strategy=strat, sources=sources, report=report,
                notebook_id=meta.get("notebook_id"), task_id=meta.get("task_id"),
                error=meta.get("error"),
            ))
        except Exception as e:  # a failed arm must not sink the whole experiment
            exp.arms.append(ArmResult(
                strategy=strat, sources=[], report=score_sources([], question, w),
                error=f"{type(e).__name__}: {e}",
            ))
    return exp


def suggest_next_prompt(prior_winners: list[str]) -> str | None:
    """v1 stub of strategy memory: reuse the most recent winning prompt as the
    baseline arm for a similar future question. Real mutation/exploration is M3."""
    return prior_winners[-1] if prior_winners else None
