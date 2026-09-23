"""
Backend-agnostic tool registration.

One definition of the 13 tools, bound onto the bundled stdlib stdio server
(`mcp_stdio.StdioMCPServer`). `server.py` can additionally bind the same callables
onto the optional official SDK, but the bundled path here needs nothing but stdlib —
that is what lets the package "fully load" without linking any external MCP repo.

Each tool is a plain function returning a JSON string; the stdio server wraps that as
MCP text content.
"""
from __future__ import annotations

import json

from .catalog import search_catalog, sync_catalog
from .mcp_stdio import StdioMCPServer
from .nlmx_adapter import NlmxAdapter, NlmxError
from .research.ab import Strategy, run_ab, suggest_next_prompt
from .research.ablation import quick_assess
from .research.evals import detect_runners, eval_skill_trend, run_adversary
from .research.evolve import EvolutionPolicy, plan_evolution
from .research.quality import QualityWeights, score_sources
from .storage import Store


def _weights_from(d) -> QualityWeights:
    """Accept None, a preset NAME (e.g. 'news'), or a dict of weight overrides."""
    if not d:
        return QualityWeights()
    if isinstance(d, str):
        from .research.quality import preset
        try:
            return preset(d)
        except KeyError:
            return QualityWeights()
    valid = {k: v for k, v in d.items() if k in QualityWeights.__dataclass_fields__}
    return QualityWeights(**valid)


class ToolLogic:
    """Holds the adapter + store and implements each tool as a method returning JSON.

    Kept separate from any MCP transport so it is unit-testable and reusable across
    the bundled stdio server and the optional SDK server.
    """

    def __init__(self, adapter: NlmxAdapter | None = None, store: Store | None = None):
        self._adapter = adapter
        self._store = store

    # lazy so importing/registering never requires nlmx or a writable DB to exist
    @property
    def adapter(self) -> NlmxAdapter:
        if self._adapter is None:
            self._adapter = NlmxAdapter()
        return self._adapter

    @property
    def store(self) -> Store:
        if self._store is None:
            self._store = Store()
        return self._store

    def run_experiment(self, question: str, strategies: list, weights: dict | None = None) -> str:
        w = _weights_from(weights)
        strats = [Strategy(**{k: v for k, v in s.items()
                              if k in Strategy.__dataclass_fields__}) for s in strategies]
        exp = run_ab(question, strats, runner=self.adapter.research_run, weights=w)
        result = exp.as_dict()
        wj = result["arms"][0]["report"]["weights"] if result["arms"] and \
            result["arms"][0]["report"] else {}
        result["experiment_id"] = self.store.record_experiment(result, weights=wj)
        return json.dumps(result, indent=2)

    def score_notebook(self, notebook_id: str, question: str = "",
                       weights: dict | None = None) -> str:
        w = _weights_from(weights)
        sources = self.adapter.list_sources(notebook_id)
        for s in sources[:15]:
            if not s.content:
                s.content = self.adapter.source_content(s.id)
        return json.dumps(score_sources(sources, question, w).as_dict(), indent=2)

    def history(self, experiment_id: int | None = None, limit: int = 20) -> str:
        if experiment_id is not None:
            return json.dumps(self.store.get_experiment(experiment_id)
                              or {"error": f"no experiment {experiment_id}"}, indent=2)
        return json.dumps(self.store.list_experiments(limit), indent=2)

    def suggest_prompt(self, question: str) -> str:
        winners = self.store.prior_winners(question)
        return json.dumps({"question": question,
                           "suggested_prompt": suggest_next_prompt(winners),
                           "based_on_prior_winners": len(winners)}, indent=2)

    def evolve_notebook(self, notebook_id: str, question: str = "", keep_budget: int = 25,
                        quality_floor: float = 0.35, apply: bool = False) -> str:
        pol = EvolutionPolicy(keep_budget=keep_budget, quality_floor=quality_floor)
        sources = self.adapter.list_sources(notebook_id)
        for s in sources[:15]:
            if not s.content:
                s.content = self.adapter.source_content(s.id)
        plan, _ = plan_evolution(notebook_id, existing=sources, question=question, policy=pol)
        out = plan.as_dict()
        from .research.apply import apply_plan
        # apply=False (default) => dry run: report what WOULD be removed, touch nothing.
        report = apply_plan(plan, self.adapter, confirm=bool(apply))
        out["execution"] = report.as_dict()
        out["applied"] = bool(apply)
        out["note"] = ("dry run — pass apply=true to actually remove the EVICT/MERGE "
                       "sources (destructive, confirm-gated)") if not apply else \
                      "applied: EVICT/MERGE sources removed via nlmx (--confirm)"
        return json.dumps(out, indent=2)

    def assess_notebook(self, notebook_id: str, question: str = "", mode: str = "fast",
                        weights: dict | None = None) -> str:
        """Lightweight (fast) or full (deep) value assessment of an existing
        notebook's CURRENT source set — same engine research_run_experiment wires
        into every arm automatically, exposed here for on-demand / deep calls.

        Content backfill is capped at 15 sources for the cheap fast lane (matches
        score_notebook/evolve_notebook), but deep mode backfills ALL sources: it
        already commits to the full O(n^2) ablation/pairwise/greedy-prune pipeline,
        so silently scoring sources 16+ as contentless would corrupt exactly the
        thorough result the caller asked for (sparring review caught this)."""
        w = _weights_from(weights)
        sources = self.adapter.list_sources(notebook_id)
        to_fill = sources if mode.lower() == "deep" else sources[:15]
        for s in to_fill:
            if not s.content:
                s.content = self.adapter.source_content(s.id)
        return json.dumps(quick_assess(sources, question, weights=w, mode=mode), indent=2)

    def eval_skill(self, strategy_name: str) -> str:
        return json.dumps(eval_skill_trend(strategy_name,
                                           self.store.arm_scores(strategy_name)).as_dict(),
                          indent=2)

    def adversarial_run(self, prompt: str, runners: list | None = None,
                        timeout: int = 300) -> str:
        specs = detect_runners()
        if runners:
            specs = [s for s in specs if s.name in set(runners)]
        results = [run_adversary(s, prompt, timeout=timeout).as_dict() for s in specs]
        return json.dumps({"prompt": prompt,
                           "available": [s.name for s in specs if s.available],
                           "results": results}, indent=2)

    def catalog_sync(self, notebook_ids: list | None = None) -> str:
        return json.dumps(sync_catalog(self.store, self.adapter,
                                       notebook_ids=notebook_ids).as_dict(), indent=2)

    def catalog_search(self, query: str, limit: int = 20) -> str:
        return json.dumps({"query": query,
                           "results": search_catalog(self.store, query, limit=limit)}, indent=2)

    def learn_topic(self, question: str, explore: int = 2) -> str:
        """M3 strategy memory: derive the next A/B arm set (winner + mutations) and a
        tuned weight profile for a topic from its experiment history; persist it."""
        from .research.strategy import profile_from_history, suggest_strategies
        winners = self.store.prior_winners(question)
        best_prompt = winners[0] if winners else None
        means = self.store.winner_signal_means(question)
        runs = self.store.experiment_count(question)
        # best_score: the top arm score of the latest experiment, if any
        best_score = 0.0
        latest = self.store.list_experiments(1)
        for e in latest:
            if e["question"] == question and e.get("winner_score"):
                best_score = e["winner_score"]
        profile = profile_from_history(question, best_prompt, best_score, means, runs)
        self.store.upsert_topic_profile(profile.as_dict())
        strategies = suggest_strategies(question, best_prompt, explore=explore)
        return json.dumps({"profile": profile.as_dict(),
                           "suggested_strategies": [s.as_dict() for s in strategies]}, indent=2)

    def spar(self, question: str, prompt: str, families: list | None = None) -> str:
        """M4: run one prompt across model families over the same question, score each,
        record per-family attribution, and return the ranked compare view.

        A family participates only if its adversarial CLI is installed (detected at
        runtime); unavailable families are recorded as such. The actual research goes
        through the nlmx path (isolated notebook per family)."""
        from .research.evals import detect_runners
        from .research.sparring import run_sparring
        detected = {r.name: r.available for r in detect_runners()}
        fams = families or list(detected.keys())

        def family_runner(family: str, p: str):
            if not detected.get(family, False):
                return [], {"available": False, "error": f"{family} not installed"}
            from .research.ab import Strategy
            sources, meta = self.adapter.research_run(
                Strategy(name=f"spar-{family}", prompt=p))
            meta["available"] = True
            return sources, meta

        session = run_sparring(question, prompt, fams, runner=family_runner)
        result = session.as_dict()
        result["sparring_id"] = self.store.record_sparring(result)
        return json.dumps(result, indent=2)

    def family_leaderboard(self) -> str:
        """Mean source-quality per model family across all sparring sessions."""
        return json.dumps({"leaderboard": self.store.family_leaderboard()}, indent=2)


# JSON-Schema input specs for each tool (used by the bundled stdio server's tools/list).
_SCHEMAS: dict[str, dict] = {
    "research_run_experiment": {"type": "object", "required": ["question", "strategies"],
        "properties": {"question": {"type": "string"},
                       "strategies": {"type": "array", "items": {"type": "object"}},
                       "weights": {"type": "object"}}},
    "research_score_notebook": {"type": "object", "required": ["notebook_id"],
        "properties": {"notebook_id": {"type": "string"}, "question": {"type": "string"},
                       "weights": {"type": "object"}}},
    "research_history": {"type": "object",
        "properties": {"experiment_id": {"type": "integer"}, "limit": {"type": "integer"}}},
    "research_suggest_prompt": {"type": "object", "required": ["question"],
        "properties": {"question": {"type": "string"}}},
    "research_evolve_notebook": {"type": "object", "required": ["notebook_id"],
        "properties": {"notebook_id": {"type": "string"}, "question": {"type": "string"},
                       "keep_budget": {"type": "integer"}, "quality_floor": {"type": "number"},
                       "apply": {"type": "boolean"}}},
    "research_assess": {"type": "object", "required": ["notebook_id"],
        "properties": {"notebook_id": {"type": "string"}, "question": {"type": "string"},
                       "mode": {"type": "string"}, "weights": {"type": "object"}}},
    "research_eval_skill": {"type": "object", "required": ["strategy_name"],
        "properties": {"strategy_name": {"type": "string"}}},
    "research_adversarial_run": {"type": "object", "required": ["prompt"],
        "properties": {"prompt": {"type": "string"},
                       "runners": {"type": "array", "items": {"type": "string"}},
                       "timeout": {"type": "integer"}}},
    "catalog_sync": {"type": "object",
        "properties": {"notebook_ids": {"type": "array", "items": {"type": "string"}}}},
    "catalog_search": {"type": "object", "required": ["query"],
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}},
    "research_learn_topic": {"type": "object", "required": ["question"],
        "properties": {"question": {"type": "string"}, "explore": {"type": "integer"}}},
    "research_spar": {"type": "object", "required": ["question", "prompt"],
        "properties": {"question": {"type": "string"}, "prompt": {"type": "string"},
                       "families": {"type": "array", "items": {"type": "string"}}}},
    "research_family_leaderboard": {"type": "object", "properties": {}},
}

_DESCRIPTIONS: dict[str, str] = {
    "research_run_experiment": "A/B-test research prompt strategies on one question; score "
        "each run's SOURCES by data quality; rank; record to the ledger.",
    "research_score_notebook": "Score the data quality of an existing notebook's source set.",
    "research_history": "List past experiments, or fetch one full result by id.",
    "research_suggest_prompt": "Reuse the winning prompt from a prior experiment on the "
        "same question.",
    "research_evolve_notebook": "Plan create/merge/skip/evict + budget over a notebook's "
        "sources (self-evolving; evictions stay behind the confirm gate).",
    "research_assess": "Value-assess a notebook's current sources: mode='fast' (cheap, "
        "good/thin/bloated + coverage gaps) or 'deep' (full ablation + redundant-pairs + "
        "greedy-prune).",
    "research_eval_skill": "Is a strategy improving or regressing over its recorded history?",
    "research_adversarial_run": "Run a prompt through installed adversarial agent CLIs "
        "(opencode, copilot, …); missing ones are skipped, not failed.",
    "catalog_sync": "Index your notebooks + sources into the local catalog.",
    "catalog_search": "Full-text search the index — what do we already know about X.",
    "research_learn_topic": "M3 strategy memory: from a topic's history, suggest the next "
        "A/B arms (winner + mutations) and a tuned per-topic weight profile; persist it.",
    "research_spar": "M4 multi-model sparring: run one prompt across model families "
        "(opencode, copilot, …) over the same question; score + attribute per family; rank.",
    "research_family_leaderboard": "Mean source-quality per model family across all "
        "sparring sessions — who finds the best sources overall.",
}

# maps public tool name -> ToolLogic method
_BINDINGS = {
    "research_run_experiment": "run_experiment",
    "research_score_notebook": "score_notebook",
    "research_history": "history",
    "research_suggest_prompt": "suggest_prompt",
    "research_evolve_notebook": "evolve_notebook",
    "research_assess": "assess_notebook",
    "research_eval_skill": "eval_skill",
    "research_adversarial_run": "adversarial_run",
    "catalog_sync": "catalog_sync",
    "catalog_search": "catalog_search",
    "research_learn_topic": "learn_topic",
    "research_spar": "spar",
    "research_family_leaderboard": "family_leaderboard",
}


def build_stdio_server(logic: ToolLogic | None = None) -> StdioMCPServer:
    """Build the bundled stdlib-only MCP server with all 13 tools registered."""
    logic = logic or ToolLogic()
    srv = StdioMCPServer("notelab")
    for public_name, method_name in _BINDINGS.items():
        srv.tool(public_name, _DESCRIPTIONS[public_name], _SCHEMAS[public_name])(
            getattr(logic, method_name))
    return srv


def tool_names() -> list[str]:
    return list(_BINDINGS)
