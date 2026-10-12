#!/usr/bin/env python3
"""One-command repro for docs/RESULTS-2026-09-23.md.

Requires a working `nlmx` on PATH (or NLMX_BIN set) authenticated against
NotebookLM. Pulls the 16 real sources this report used out of the notebook named
by the NOTELAB_REPRO_NOTEBOOK_ID environment variable, by title match, fetches
their real content, and runs the same score_sources / quick_assess(fast) /
quick_assess(deep) calls documented in the report. Read-only — no notebook mutation.

    NOTELAB_REPRO_NOTEBOOK_ID=<your-notebook-id> python3 scripts/repro_results_2026-09-23.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, "src")
from notelab.research.ablation import quick_assess  # noqa: E402
from notelab.research.quality import SourceFeatures, score_sources  # noqa: E402

NOTEBOOK_ID = os.environ.get("NOTELAB_REPRO_NOTEBOOK_ID", "")
TITLE_TERMS = ("judge", "bias", "rater", "scoring", "evaluat")
Q1 = "LLM-as-a-judge bias mitigation strategies"
Q2 = "central tendency bias in clinical ordinal scoring by multimodal LLM raters"


def _nlmx(*args: str, timeout: int = 60) -> str:
    r = subprocess.run(["nlmx", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"nlmx {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def main() -> None:
    if not NOTEBOOK_ID:
        sys.exit("set NOTELAB_REPRO_NOTEBOOK_ID to the notebook you want to reproduce from")
    listing = json.loads(_nlmx("source", "list", NOTEBOOK_ID, "--json", timeout=60))
    picked = [s for s in listing
              if any(t in (s.get("title") or "").lower() for t in TITLE_TERMS)]
    print(f"{len(picked)} sources matched (of {len(listing)} in the notebook)", file=sys.stderr)

    sources: list[SourceFeatures] = []
    for s in picked:
        content = _nlmx("source", "content", s["id"], timeout=180)
        sources.append(SourceFeatures(id=s["id"], title=s.get("title") or "",
                                      url=s.get("url", ""), content=content))
        print(f"  fetched {s['id'][:8]} ({len(content)} chars): {s.get('title')}", file=sys.stderr)

    for label, q in (("Q1 (broad)", Q1), ("Q2 (specific)", Q2)):
        report = score_sources(sources, question=q)
        fast = quick_assess(sources, q, mode="fast")
        deep = quick_assess(sources, q, mode="deep")
        load_bearing = [p["id"] for p in deep["ablation"]["per_source"] if p["verdict"] == "load-bearing"]
        print(f"\n=== {label}: {q!r} ===")
        print(f"composite={report.quality_score:.4f}  fast_verdict={fast['verdict']}  "
              f"escalate_to_deep={fast['escalate_to_deep']}")
        print(f"deep: load_bearing={load_bearing}  "
              f"redundant_pairs={len(deep['redundant_pairs'])}  "
              f"greedy_kept={deep['greedy_prune']['kept_ids']}")


if __name__ == "__main__":
    main()
