"""nlm-research-mcp — a self-improving research MCP server on top of NotebookLM."""
from __future__ import annotations

__version__ = "0.1.0"

from .catalog import search_catalog, sync_catalog
from .research.ab import ArmResult, Experiment, Strategy, run_ab
from .research.evals import Trend, detect_runners, eval_skill_trend, run_adversary
from .research.evolve import Action, EvolutionPolicy, plan_evolution
from .research.apply import ApplyReport, apply_plan
from .research.strategy import (
    TopicProfile,
    profile_from_history,
    suggest_strategies,
    tune_weights_from_winner,
)
from .research.sparring import FamilyResult, SparringSession, run_sparring
from .research.quality import (
    QualityReport,
    QualityWeights,
    SourceFeatures,
    score_sources,
)
from .storage import Store

__all__ = [
    "__version__",
    "Strategy", "run_ab", "Experiment", "ArmResult",
    "SourceFeatures", "QualityWeights", "QualityReport", "score_sources",
    "Action", "EvolutionPolicy", "plan_evolution", "apply_plan", "ApplyReport",
    "TopicProfile", "suggest_strategies", "tune_weights_from_winner", "profile_from_history",
    "SparringSession", "FamilyResult", "run_sparring",
    "Trend", "eval_skill_trend", "detect_runners", "run_adversary",
    "sync_catalog", "search_catalog",
    "Store",
]
