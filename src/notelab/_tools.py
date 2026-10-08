"""
Backend-agnostic tool registration.

One definition of the 14 tools, bound onto the bundled stdlib stdio server
(`mcp_stdio.StdioMCPServer`). `server.py` can additionally bind the same callables
onto the optional official SDK, but the bundled path here needs nothing but stdlib —
that is what lets the package "fully load" without linking any external MCP repo.

Each tool is a plain function returning a JSON string; the stdio server wraps that as
MCP text content.
"""
from __future__ import annotations

import json
from pathlib import Path

from .catalog import search_catalog, sync_catalog
from .mcp_stdio import StdioMCPServer
from .nlmx_adapter import NlmxAdapter, NlmxError
from .research.ab import Strategy, run_ab, suggest_next_prompt
from .research.ablation import quick_assess
from .research.evals import (
    detect_runners,
    detect_runners_with_reliability,
    eval_skill_trend,
    run_adversary,
)
from .research.evolve import EvolutionPolicy, plan_evolution
from .research.quality import QualityWeights, score_sources
from .research.sparring import _sources_from_adversary_output, run_sparring
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


def _notelab_repo_root() -> str:
    return str(Path(__file__).resolve().parents[2])


def _with_semantic_expansions(question, sources, w):
    """Opt-in semantic relevance: enrich weights with live Mistral term
    expansions. Returns (weights, error_payload_or_None) — a missing key or
    API failure surfaces as a JSON error, never an exception, so the caller
    can fall back to the default stdlib-only path."""
    from .research.semantic import SemanticExpansionError, semantic_term_expansions
    try:
        result = semantic_term_expansions(question, sources)
    except SemanticExpansionError as e:
        return w, {"error": str(e), "hint": "set MISTRAL_API_KEY or drop "
                                            "semantic_expansions for the stdlib-only path."}
    w.term_expansions = result.expansions
    return w, None


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
                       weights: dict | None = None,
                       semantic_expansions: bool = False) -> str:
        w = _weights_from(weights)
        sources = self.adapter.list_sources(notebook_id)
        for s in sources[:15]:
            if not s.content:
                s.content = self.adapter.source_content(s.id)
        if semantic_expansions:
            w, err = _with_semantic_expansions(question, sources, w)
            if err is not None:
                return json.dumps(err, indent=2)
        report = score_sources(sources, question, w).as_dict()
        if semantic_expansions:
            report["semantic_expansions_applied"] = True
            report["semantic_expansion_count"] = sum(len(v) for v in w.term_expansions.values())
        return json.dumps(report, indent=2)

    def ingest_codebase(self, repo: str, notebook_id: str | None = None,
                        title: str | None = None, max_files: int = 200,
                        mode: str = "digest", max_sources: int = 40) -> str:
        """Ingest a codebase (GitHub URL or local path) and add its digest as a
        NotebookLM source. Positive capability to pair with evolve_notebook's
        curation: this ADDS knowledge (structure, APIs, quality signals) where
        evolve only prunes. Without notebook_id, creates '[codebase] <name>'.

        The digest text is bounded (structure summaries, capped lists) but a
        pathological repo could still balloon argv, so text is capped at
        100k chars with a visible truncation note.

        mode="digest" (default): one structure/API summary source. Good for orientation,
        but it holds no source code, so a notebook built from it cannot answer detail
        questions. mode="files": the real files, bundled ~90k chars per source with
        `# FILE: path` headers (at most `max_sources`; NotebookLM caps sources per
        notebook). Pair with code_index/code_find for exact, instant lookups."""
        from .research.codebase_ingest import CodebaseIngester, bundle_files
        if mode not in ("digest", "files"):
            return json.dumps({"error": "mode must be 'digest' or 'files'"}, indent=2)
        try:
            digest = CodebaseIngester(
                max_files=max_files,
                max_file_lines=500 if mode == "digest" else 1_000_000).ingest(repo)
        except Exception as e:  # ValueError (bad URL/path) or network failure
            return json.dumps({"error": f"ingest failed for {repo!r}: {e}"}, indent=2)
        if not digest.files:
            return json.dumps({"error": f"ingest of {repo!r} found no ingestible "
                                        "files — nothing added. Check the path/URL and "
                                        "skip patterns."}, indent=2)
        if mode == "files":
            bundles = bundle_files(digest.files)
            if len(bundles) > max_sources:
                return json.dumps({"error": f"{digest.repo_name} needs {len(bundles)} sources "
                    f"(> max_sources={max_sources}). Index it locally with code_index instead, "
                    "or ingest a subdirectory / raise max_sources within your NotebookLM plan."}, indent=2)
            try:
                nb = notebook_id or self.adapter.create_notebook(f"[codebase] {digest.repo_name}")
                results = [self.adapter.add_source(nb, text, title=f"{digest.repo_name} {t}")
                           for t, text in bundles]
            except NlmxError as e:
                return json.dumps({"error": str(e)}, indent=2)
            failed = [bundles[i][0] for i, (ok, _m) in enumerate(results) if not ok]
            return json.dumps({"repo": repo, "repo_name": digest.repo_name, "mode": "files",
                               "files": len(digest.files), "sources_added": len(bundles) - len(failed),
                               "sources_failed": failed, "notebook_id": nb,
                               "total_lines": digest.total_lines}, indent=2)
        text = digest.to_text()
        truncated = False
        if len(text) > 100_000:
            text = text[:100_000] + "\n\n*... digest truncated at 100k chars*"
            truncated = True
        try:
            nb = notebook_id or self.adapter.create_notebook(
                f"[codebase] {digest.repo_name}")
            ok, msg = self.adapter.add_source(
                nb, text, title=title or f"Codebase: {digest.repo_name}")
        except NlmxError as e:
            return json.dumps({"error": str(e)}, indent=2)
        return json.dumps({
            "repo": repo, "repo_name": digest.repo_name,
            "default_branch": digest.default_branch,
            "files": len(digest.files), "total_lines": digest.total_lines,
            "languages": digest.languages,
            "test_file_ratio": digest.test_file_ratio,
            "doc_coverage": digest.doc_coverage,
            "digest_chars": len(text), "truncated": truncated,
            "notebook_id": nb, "added": ok, "message": msg,
        }, indent=2)

    _BLOCKED = ("not a bot", "sign in to confirm", "unusual traffic", "enable javascript")

    def fix_videos(self, notebook_id: str, apply: bool = False, remove_junk: bool = False) -> str:
        """Repair video sources. Deep Research imports YouTube links as web pages, and we
        observed those copies hold a 'Sign in to confirm you're not a bot' wall instead of the
        transcript, while a native `source add --youtube` copy holds the real transcript.
        Plan (default): list every youtube.com / youtu.be source, flag blocked copies, and say
        which already have a native copy. apply=true adds the missing native copies (additive,
        safe). remove_junk=true (needs apply) also deletes the blocked copies, a destructive step."""
        import re
        try:
            sources = self.adapter.list_sources(notebook_id)
        except NlmxError as e:
            return json.dumps({"error": str(e)}, indent=2)
        key = lambda t: " ".join(t.lower().split())[:40]  # titles get truncated differently per copy
        native_titles = {key(s.title) for s in sources if not s.url}
        rows = []
        for s in sources:
            if not re.search(r"(?:youtube\.com|youtu\.be)/", s.url or ""):
                continue
            text = (s.content or self.adapter.source_content(s.id)).lower()
            blocked = any(m in text for m in self._BLOCKED)
            has_native = key(s.title) in native_titles
            action = ("already_native" if has_native else "add_native") if blocked else "ok"
            rows.append({"source_id": s.id, "title": s.title, "url": s.url,
                         "blocked_page": blocked, "has_native_copy": has_native, "action": action})
        results = []
        if apply:
            for r in rows:
                if r["action"] == "add_native":
                    ok, msg = self.adapter.add_youtube(notebook_id, r["url"].split("&")[0])
                    results.append({"url": r["url"], "added": ok, "message": msg})
                if remove_junk and r["blocked_page"] and (r["has_native_copy"] or any(
                        x["url"] == r["url"] and x["added"] for x in results)):
                    ok, msg = self.adapter.remove_source(notebook_id, r["source_id"])
                    results.append({"source_id": r["source_id"], "removed": ok, "message": msg})
        return json.dumps({"notebook_id": notebook_id, "videos": len(rows),
                           "blocked": sum(r["blocked_page"] for r in rows), "plan": rows,
                           "applied": results if apply else None}, indent=2)

    def _code_index(self):
        from .codeindex import CodeIndex
        return CodeIndex(Path(self.store.path).with_name("code.db"))

    def code_index(self, repo: str, name: str | None = None, version: str | None = None,
                   max_files: int = 5000) -> str:
        """Index a repo (local path or GitHub URL) as a new, kept version (default label: git
        commit). Older versions stay queryable; re-indexing the same version replaces it."""
        try:
            return json.dumps(self._code_index().index(repo, name, version, max_files), indent=2)
        except Exception as e:
            return json.dumps({"error": f"index failed for {repo!r}: {e}"}, indent=2)

    def code_find(self, query: str, kind: str = "any", repo: str | None = None, limit: int = 5,
                  version: str | None = None) -> str:
        """Targeted code lookup over indexed repos: kind = def | callers | text | any.
        version: a label from code_versions, or '*' for all; default is each repo's newest."""
        from .codeindex import render
        if kind not in ("any", "def", "callers", "subclasses", "text"):
            return json.dumps({"error": "kind must be any|def|callers|subclasses|text"}, indent=2)
        ci = self._code_index()
        if not ci.repos():
            return json.dumps({"error": "nothing indexed yet: call code_index(repo) first"}, indent=2)
        return render(ci.find(query, kind, repo, limit, version))

    def code_versions(self, repo: str | None = None) -> str:
        """List every kept version of indexed repos (nothing is forgotten)."""
        return json.dumps(self._code_index().versions(repo), indent=2)

    def code_diff(self, repo: str, old: str, new: str) -> str:
        """What changed between two indexed versions: files and functions added/removed/changed."""
        try:
            return json.dumps(self._code_index().diff(repo, old, new), indent=2)
        except ValueError as e:
            return json.dumps({"error": str(e)}, indent=2)

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
                        weights: dict | None = None, use_laya: bool = False,
                        semantic_expansions: bool = False) -> str:
        """Lightweight (fast) or full (deep) value assessment of an existing
        notebook's CURRENT source set — same engine research_run_experiment wires
        into every arm automatically, exposed here for on-demand / deep calls.

        Content backfill is capped at 15 sources for the cheap fast lane (matches
        score_notebook/evolve_notebook), but deep mode backfills ALL sources: it
        already commits to the full O(n^2) ablation/pairwise/greedy-prune pipeline,
        so silently scoring sources 16+ as contentless would corrupt exactly the
        thorough result the caller asked for (sparring review caught this).

        use_laya: opt-in tie-breaker for ablation's ambiguous zone (mode="deep"
        only, ignored in mode="fast" — see quick_assess). Requires laya-mlx
        installed (pip install 'notelab[laya]'); raises LayaJudgeError if not.

        semantic_expansions: opt-in Mistral-embedding term expansions folded
        into relevance scoring (both modes). Requires MISTRAL_API_KEY; any
        failure returns a JSON error suggesting the default stdlib-only path."""
        w = _weights_from(weights)
        sources = self.adapter.list_sources(notebook_id)
        to_fill = sources if mode.lower() == "deep" else sources[:15]
        for s in to_fill:
            if not s.content:
                s.content = self.adapter.source_content(s.id)
        if semantic_expansions:
            w, err = _with_semantic_expansions(question, sources, w)
            if err is not None:
                return json.dumps(err, indent=2)
        out = quick_assess(sources, question, weights=w, mode=mode, use_laya=use_laya)
        if semantic_expansions:
            out["semantic_expansions_applied"] = True
            out["semantic_expansion_count"] = sum(len(v) for v in w.term_expansions.values())
        return json.dumps(out, indent=2)

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
        best_score = self.store.latest_winner_score(question)
        profile = profile_from_history(question, best_prompt, best_score, means, runs)
        self.store.upsert_topic_profile(profile.as_dict())
        strategies = suggest_strategies(question, best_prompt, explore=explore)
        return json.dumps({"profile": profile.as_dict(),
                           "suggested_strategies": [s.as_dict() for s in strategies]}, indent=2)

    def spar(self, question: str, prompt: str, families: list | None = None) -> str:
        """M4: run one prompt across model families over the same question, score each,
        record per-family attribution, and return the ranked compare view.

        A family participates only if its adversarial CLI is installed (detected at
        runtime); unavailable families are recorded as such. Each available family's
        research goes through ITS OWN CLI via run_adversary -- not a shared
        NotebookLM path. Fixes FIELD-NOTES.md gap #9: previously every family routed
        through the identical `self.adapter.research_run(...)` call regardless of
        which CLI was detected, so the "which family finds the best sources" ranking
        never actually compared model families at all."""
        specs = {s.name: s for s in detect_runners_with_reliability()}
        fams = families or list(specs.keys())

        def family_runner(family: str, p: str):
            spec = specs.get(family)
            if spec is None or not spec.available:
                # spec.skipped_reason carries a REAL diagnostic (e.g. a reliability-
                # filter exclusion for quota exhaustion) when one is available -- only
                # fall back to the generic "not installed" when the binary genuinely
                # isn't on PATH and no filter has anything more specific to say.
                reason = spec.skipped_reason if spec is not None else None
                return [], {"available": False, "error": reason or f"{family} not installed"}
            result = run_adversary(spec, p, timeout=180, cwd=_notelab_repo_root())
            if result.timed_out:
                return [], {"available": True, "error": "timed out"}
            if result.skipped_reason:
                return [], {"available": True, "error": result.skipped_reason}
            sources = _sources_from_adversary_output(result, question=p)
            # returncode == 0 is the only real-success case. A nonzero code is a
            # real process error; None means run_adversary hit an exception path
            # (Popen spawn failure, a communicate() exception) -- both carry a real
            # diagnostic in stderr and must NOT be treated the same as "no error"
            # just because None and 0 are both falsy.
            error = None if result.returncode == 0 else (result.stderr[:300] or "unknown error")
            return sources, {"available": True, "error": error}

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
                       "weights": {"type": "object"},
                       "semantic_expansions": {"type": "boolean"}}},
    "research_ingest_codebase": {"type": "object", "required": ["repo"],
        "properties": {"repo": {"type": "string"}, "notebook_id": {"type": "string"},
                       "title": {"type": "string"}, "max_files": {"type": "integer"},
                       "mode": {"type": "string", "enum": ["digest", "files"]},
                       "max_sources": {"type": "integer"}}},
    "research_fix_videos": {"type": "object", "required": ["notebook_id"],
        "properties": {"notebook_id": {"type": "string"}, "apply": {"type": "boolean"},
                       "remove_junk": {"type": "boolean"}}},
    "code_index": {"type": "object", "required": ["repo"],
        "properties": {"repo": {"type": "string"}, "name": {"type": "string"},
                       "version": {"type": "string"}, "max_files": {"type": "integer"}}},
    "code_versions": {"type": "object", "properties": {"repo": {"type": "string"}}},
    "code_diff": {"type": "object", "required": ["repo", "old", "new"],
        "properties": {"repo": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}}},
    "code_find": {"type": "object", "required": ["query"],
        "properties": {"query": {"type": "string"},
                       "kind": {"type": "string", "enum": ["any", "def", "callers", "subclasses", "text"]},
                       "repo": {"type": "string"}, "limit": {"type": "integer"},
                       "version": {"type": "string"}}},
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
                       "mode": {"type": "string"}, "weights": {"type": "object"},
                       "use_laya": {"type": "boolean"},
                       "semantic_expansions": {"type": "boolean"}}},
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
    "research_ingest_codebase": "Ingest a codebase (GitHub URL or local path) and "
        "add it to NotebookLM; creates a notebook unless notebook_id is given. "
        "mode='digest' (default) adds a structure summary only; mode='files' adds the real "
        "source files so the notebook can answer detail questions.",
    "code_index": "Index a repo (local path or GitHub URL) locally for exact, zero-token code "
        "search. Run once per repo; re-run to refresh.",
    "research_fix_videos": "Repair video sources: Deep Research imports YouTube links as web pages "
        "that can hold a bot-wall instead of the transcript. Plans (default) or re-adds them natively "
        "so NotebookLM fetches the real transcript; deleting the blocked copies needs remove_junk=true.",
    "code_versions": "List every kept version of indexed repos: nothing is forgotten.",
    "code_diff": "What changed between two indexed versions of a repo: files and functions "
        "added, removed and changed.",
    "code_find": "Targeted code lookup over indexed repos, instant and verbatim: kind='def' "
        "(where is X defined, with exact source), 'callers' (every call site of X), "
        "'subclasses' (direct subclasses of X), 'text' (ranked full-text search), or 'any'.",
    "research_history": "List past experiments, or fetch one full result by id.",
    "research_suggest_prompt": "Reuse the winning prompt from a prior experiment on the "
        "same question.",
    "research_evolve_notebook": "Plan create/merge/skip/evict + budget over a notebook's "
        "sources (self-evolving; evictions stay behind the confirm gate).",
    "research_assess": "Value-assess a notebook's current sources: mode='fast' (cheap, "
        "good/thin/bloated + coverage gaps) or 'deep' (full ablation + redundant-pairs + "
        "greedy-prune). use_laya=true (deep mode only) opts into a Laya tie-breaker for "
        "ambiguous redundant/load-bearing calls — requires laya-mlx installed.",
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
    "research_ingest_codebase": "ingest_codebase",
    "research_fix_videos": "fix_videos",
    "code_index": "code_index",
    "code_find": "code_find",
    "code_versions": "code_versions",
    "code_diff": "code_diff",
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
    """Build the bundled stdlib-only MCP server with all 19 tools registered."""
    logic = logic or ToolLogic()
    srv = StdioMCPServer("notelab")
    for public_name, method_name in _BINDINGS.items():
        srv.tool(public_name, _DESCRIPTIONS[public_name], _SCHEMAS[public_name])(
            getattr(logic, method_name))
    return srv


def tool_names() -> list[str]:
    return list(_BINDINGS)
