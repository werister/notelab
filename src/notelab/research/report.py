"""
Turn a scored notebook into an ACTIONABLE research report.

NoteLab's scorers emit numbers; this module turns those numbers into something a
human (or an agent) can act on: a verdict, the source-kind mix, the specific problems
found (blocked videos, coverage gaps, redundant sources), and concrete NEXT ACTIONS.
It is pure and deterministic — it reads the dicts that `score_sources` / `quick_assess`
already produce (no network, no new scoring), so it stays stdlib-only and offline.

Three renderings share one assembled model:
  - markdown: the human-readable actionable report (the default, the point of this).
  - json: the same assembled model, machine-readable.
  - csv: one row per source (id, kind, domain, signals, blocked) for a spreadsheet.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any


# --- Actionable interpretation -------------------------------------------------
# These thresholds mirror the scorer's own language (quick_assess verdicts, the
# quality floor) so the report's advice is consistent with the numbers.

def _findings_and_actions(report: dict, assess: dict | None) -> tuple[list[str], list[str]]:
    """Derive concrete findings (what's wrong/notable) and next actions (what to do)
    from the scored report + optional assessment. Everything here is explainable from
    a field in the input dicts — no hidden judgement."""
    findings: list[str] = []
    actions: list[str] = []

    per_source = report.get("per_source", [])
    type_mix = report.get("type_mix", {})
    n = report.get("n_sources", len(per_source))

    # 1. Blocked / bot-wall sources — the live-verified failure mode.
    blocked = [p for p in per_source if p.get("blocked")]
    if blocked:
        kinds = sorted({p.get("kind", "web") for p in blocked})
        findings.append(
            f"{len(blocked)} source(s) look like blocked bot-wall / sign-in shells "
            f"(kinds: {', '.join(kinds)}) — they hold no real content."
        )
        actions.append(
            "Run `research_fix_videos(notebook_id, apply=true)` to re-add blocked "
            "videos natively so their real transcript is used."
        )

    # 2. Kind-mix lopsidedness — the diversity story, made actionable.
    present = {k for k, c in type_mix.items() if c > 0 and k != "web"}
    web_heavy = type_mix.get("web", 0) >= max(1, n * 0.6)
    missing_valued = [k for k in ("paper", "video", "discussion", "docs")
                      if k not in present]
    if n >= 3 and (web_heavy or len(present) <= 1) and missing_valued:
        findings.append(
            f"Source mix is lopsided (type_mix={type_mix}); "
            f"low variety across source kinds."
        )
        actions.append(
            "If you want a broader library, run another arm that explicitly asks for "
            f"the missing kinds (e.g. {', '.join(missing_valued[:3])}) — a step-by-step "
            "video, a community thread, or a primary paper."
        )

    # 3. Coverage gaps (from assessment, if provided).
    if assess:
        gaps = assess.get("coverage_gaps") or []
        if gaps:
            findings.append(f"Question terms not covered by any source: {', '.join(gaps)}.")
            actions.append(
                f"Research again targeting the uncovered terms ({', '.join(gaps[:4])}) "
                "or add a source that addresses them."
            )
        verdict = assess.get("verdict")
        if verdict == "thin":
            actions.append("Assessment says 'thin' — gather more / higher-quality sources "
                           "before relying on this notebook.")
        elif verdict == "bloated":
            actions.append("Assessment says 'bloated' — consider "
                           "`research_evolve_notebook(..., apply=true)` to prune redundant sources.")
        # deep-mode redundancy
        redundant = assess.get("redundant_pairs") or []
        if redundant:
            findings.append(f"{len(redundant)} redundant source pair(s) detected (deep assess).")
            prune = assess.get("greedy_prune") or {}
            kept = prune.get("kept_ids")
            if kept is not None:
                actions.append(
                    f"Greedy prune keeps {len(kept)}/{n} sources at the same coverage — "
                    "`research_evolve_notebook` can apply this (confirm-gated)."
                )

    # 4. Low composite overall.
    q = report.get("quality_score", 0.0)
    if n > 0 and q < 0.45:
        findings.append(f"Overall source-quality score is low ({q}).")
        actions.append("The sources are weak for this question — try a more "
                       "authoritative or specific research prompt (A/B two with "
                       "`research_run_experiment`).")

    if not findings:
        findings.append("No blocking issues: no blocked shells, acceptable kind mix, "
                        "coverage looks complete.")
    if not actions:
        actions.append("Looks solid — query the notebook for cited answers, or score "
                       "another prompt to see if you can beat this set.")
    return findings, actions


def build_report_model(report: dict, *, notebook_id: str = "", question: str = "",
                       assess: dict | None = None) -> dict:
    """Assemble the single report model all three renderings share."""
    findings, actions = _findings_and_actions(report, assess)
    model = {
        "notebook_id": notebook_id,
        "question": question,
        "quality_score": report.get("quality_score"),
        "verdict": (assess or {}).get("verdict"),
        "n_sources": report.get("n_sources"),
        "type_mix": report.get("type_mix", {}),
        "type_diversity": report.get("type_diversity"),
        "domain_diversity": report.get("domain_diversity"),
        "signal_means": report.get("signal_means", {}),
        "weights": report.get("weights", {}),
        "findings": findings,
        "next_actions": actions,
        "per_source": report.get("per_source", []),
    }
    if assess:
        model["coverage"] = assess.get("coverage")
        model["coverage_gaps"] = assess.get("coverage_gaps")
    return model


# --- Renderers -----------------------------------------------------------------

def render_markdown(model: dict) -> str:
    """The actionable human report."""
    q = model.get("quality_score")
    lines = [
        f"# Research report{': ' + model['question'] if model.get('question') else ''}",
        "",
    ]
    if model.get("notebook_id"):
        lines.append(f"**Notebook:** `{model['notebook_id']}`")
    lines.append(f"**Overall source quality:** {q if q is not None else 'n/a'}"
                 f"{'  ·  verdict: ' + model['verdict'] if model.get('verdict') else ''}")
    lines.append(f"**Sources:** {model.get('n_sources', 0)}  ·  "
                 f"kind-diversity {model.get('type_diversity')}  ·  "
                 f"domain-diversity {model.get('domain_diversity')}")
    if model.get("coverage") is not None:
        lines.append(f"**Question coverage:** {model['coverage']}")

    # Source-kind mix
    mix = model.get("type_mix") or {}
    if mix:
        mix_str = ", ".join(f"{k}: {c}" for k, c in sorted(mix.items(), key=lambda x: -x[1]))
        lines.extend(["", "## Source mix", "", mix_str])

    # Per-signal breakdown (auditable)
    means = model.get("signal_means") or {}
    if means:
        lines.extend(["", "## Signal breakdown", ""])
        for sig in ("authority", "depth", "recency", "relevance", "diversity"):
            if sig in means:
                lines.append(f"- {sig}: {means[sig]}")

    # Findings
    lines.extend(["", "## What stands out", ""])
    for f in model.get("findings", []):
        lines.append(f"- {f}")

    # Next actions — the actionable part
    lines.extend(["", "## Next actions", ""])
    for i, a in enumerate(model.get("next_actions", []), 1):
        lines.append(f"{i}. {a}")

    # Per-source table
    per = model.get("per_source") or []
    if per:
        lines.extend(["", "## Sources", "",
                      "| kind | domain | authority | depth | relevance | note |",
                      "|---|---|---|---|---|---|"])
        for p in per:
            note = "⚠ blocked shell" if p.get("blocked") else ""
            lines.append(
                f"| {p.get('kind','')} | {p.get('domain','')} | {p.get('authority','')} "
                f"| {p.get('depth','')} | {p.get('relevance','')} | {note} |"
            )
    return "\n".join(lines)


def render_csv(model: dict) -> str:
    """One row per source — for a spreadsheet."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "kind", "domain", "authority", "depth", "recency",
                "relevance", "blocked"])
    for p in model.get("per_source", []):
        w.writerow([p.get("id", ""), p.get("kind", ""), p.get("domain", ""),
                    p.get("authority", ""), p.get("depth", ""), p.get("recency", ""),
                    p.get("relevance", ""), bool(p.get("blocked", False))])
    return buf.getvalue()


def render(model: dict, fmt: str = "markdown") -> str:
    """Render the assembled model in the requested format."""
    fmt = (fmt or "markdown").lower()
    if fmt == "markdown":
        return render_markdown(model)
    if fmt == "json":
        return json.dumps(model, indent=2)
    if fmt == "csv":
        return render_csv(model)
    raise ValueError(f"unknown report format {fmt!r}; choose markdown, json, or csv")
