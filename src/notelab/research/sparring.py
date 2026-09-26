"""
M4 — multi-model-family sparring.

Several *runners* (model families — opencode, copilot, claude, …) research the SAME
question over one shared corpus, and each family's result is scored and attributed to
that family in the ledger. The compare view then ranks families on the source-quality
signal, so you can see which family finds the best sources for which kind of question.

This layers on the existing pieces: the quality scorer (score each family's sources),
the adversarial-runner detection (`evals.detect_runners`), and the A/B machinery
(each family is an arm). The new part is *attribution* — a sparring session groups the
arms by family and records who won.

Execution is injected as a `FamilyRunner` callable so this is offline-testable; in
production it wraps the adversarial CLIs + the nlmx research path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .evals import RunnerResult
from .quality import QualityReport, QualityWeights, SourceFeatures, score_sources


@dataclass
class FamilyResult:
    family: str                     # model family / runner name, e.g. "opencode"
    available: bool
    sources: list[SourceFeatures] = field(default_factory=list)
    report: QualityReport | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return {"family": self.family, "available": self.available,
                "error": self.error,
                "report": self.report.as_dict() if self.report else None}


# family -> (sources, meta). meta may carry {"available": bool, "error": str}.
FamilyRunner = Callable[[str, str], tuple[list[SourceFeatures], dict]]


_URL_RE = re.compile(r"https?://\S+")


def _sources_from_adversary_output(result: RunnerResult, question: str) -> list[SourceFeatures]:
    """Turn one adversarial CLI's raw stdout into SourceFeatures score_sources() can
    rank. Real answers rarely come back as structured JSON -- extract cited URLs with
    a bounded content snippet around each; if the CLI answered in prose with no links
    at all, that's still a real (if weaker) outcome, not a non-event, so the whole
    stdout becomes one sourceless SourceFeatures rather than an empty list. A
    genuinely empty answer still yields an empty list -- there is nothing to score."""
    text = result.stdout or ""
    urls = _URL_RE.findall(text)
    if not urls:
        if not text.strip():
            return []
        return [SourceFeatures(id=f"{result.runner}-0", title=f"{result.runner} answer",
                               url="", content=text[:2000])]
    out = []
    for i, url in enumerate(urls):
        idx = text.find(url)
        start = max(0, idx - 200)
        end = min(len(text), idx + len(url) + 200)
        out.append(SourceFeatures(id=f"{result.runner}-{i}", title=f"{result.runner} source {i}",
                                  url=url, content=text[start:end]))
    return out


@dataclass
class SparringSession:
    question: str
    prompt: str
    results: list[FamilyResult] = field(default_factory=list)
    weights: QualityWeights = field(default_factory=QualityWeights)

    @property
    def ranking(self) -> list[FamilyResult]:
        scored = [r for r in self.results if r.report and r.error is None]
        return sorted(scored, key=lambda r: r.report.quality_score, reverse=True)

    @property
    def winner(self) -> FamilyResult | None:
        r = self.ranking
        return r[0] if r else None

    def as_dict(self) -> dict:
        rank = self.ranking
        margin = None
        if len(rank) >= 2:
            margin = round(rank[0].report.quality_score - rank[1].report.quality_score, 4)
        return {
            "question": self.question,
            "prompt": self.prompt,
            "families_run": [r.family for r in self.results],
            "winner_family": self.winner.family if self.winner else None,
            "winner_score": round(self.winner.report.quality_score, 4) if self.winner else None,
            "margin_over_runner_up": margin,
            "compare": [
                {"family": r.family, "quality_score": round(r.report.quality_score, 4),
                 "n_sources": r.report.n_sources}
                for r in rank
            ],
            "results": [r.as_dict() for r in self.results],
        }


def run_sparring(
    question: str,
    prompt: str,
    families: list[str],
    runner: FamilyRunner,
    weights: QualityWeights | None = None,
) -> SparringSession:
    """Run one prompt across several model families over the same question, score each,
    and return an attributed, ranked session. A family that is unavailable or errors is
    recorded (available=False / error set), never allowed to sink the session."""
    w = weights or QualityWeights()
    session = SparringSession(question=question, prompt=prompt, weights=w)
    for fam in families:
        try:
            sources, meta = runner(fam, prompt)
            if meta.get("available") is False:
                session.results.append(FamilyResult(fam, available=False,
                                                     error=meta.get("error", "unavailable")))
                continue
            report = score_sources(sources, question=question, weights=w)
            session.results.append(FamilyResult(fam, available=True, sources=sources,
                                                 report=report, error=meta.get("error")))
        except Exception as e:
            session.results.append(FamilyResult(fam, available=True,
                                                error=f"{type(e).__name__}: {e}"))
    return session
