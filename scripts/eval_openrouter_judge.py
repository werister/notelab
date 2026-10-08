#!/usr/bin/env python3
"""Optional LLM-judge eval: does a real model agree with NoteLab's deterministic
source-kind classification and ranking?

NoteLab's scorer is deliberately LLM-free and reproducible. This script does NOT
change that — it is an *external audit* you can run occasionally to sanity-check the
deterministic signals against a frontier model's opinion. It never touches the engine
or its tests.

Uses OpenRouter (https://openrouter.ai) over stdlib urllib — zero new pip deps. The
only requirement is an OPENROUTER_API_KEY at call time. Default model is the free tier
(`openrouter/free`); override with OPENROUTER_MODEL. No key -> clean exit with a
message, never a crash and never a fabricated result.

    OPENROUTER_API_KEY=... python3 scripts/eval_openrouter_judge.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, "src")
from notelab.research.quality import (  # noqa: E402
    SourceFeatures, classify_source_type, score_sources, preset,
)

_URL = "https://openrouter.ai/api/v1/chat/completions"
_MODEL = os.environ.get("OPENROUTER_MODEL", "openrouter/free")

# A small, fixed audit set: one source per kind, each an unambiguous real-world example.
AUDIT = [
    ("https://arxiv.org/abs/1706.03762", "Attention Is All You Need", "paper"),
    ("https://docs.python.org/3/library/asyncio.html", "asyncio docs", "docs"),
    ("https://github.com/langchain-ai/langchain", "langchain repo", "repo"),
    ("https://www.youtube.com/watch?v=aircAruvnKk", "3Blue1Brown neural nets", "video"),
    ("https://www.reddit.com/r/MachineLearning/comments/x", "r/ML discussion", "discussion"),
    ("https://en.wikipedia.org/wiki/Transformer", "Wikipedia: Transformer", "reference"),
]


def _call_openrouter(prompt: str, key: str, timeout: float = 60.0) -> str:
    body = json.dumps({
        "model": _MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        _URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    return data["choices"][0]["message"]["content"]


def classification_agreement(key: str) -> None:
    """Ask the model to classify each URL into NoteLab's taxonomy; compare to the
    deterministic classifier. Reports agreement rate — a low rate flags taxonomy drift."""
    kinds = "paper, docs, repo, video, discussion, reference, web"
    agree = 0
    print(f"\n=== Classification agreement ({_MODEL}) ===")
    for url, title, _expected in AUDIT:
        ours = classify_source_type(url, title)
        prompt = (
            f"Classify this source into exactly one of: {kinds}. "
            f"Reply with only the single word.\nURL: {url}\nTitle: {title}"
        )
        try:
            theirs = _call_openrouter(prompt, key).strip().lower().split()[0]
        except (urllib.error.URLError, KeyError, IndexError, TimeoutError) as e:
            print(f"  [skip] {url} — call failed: {type(e).__name__}")
            continue
        match = "OK " if theirs == ours else "DIFF"
        agree += theirs == ours
        print(f"  {match}  ours={ours:<11} model={theirs:<11} {url}")
    print(f"  agreement: {agree}/{len(AUDIT)}")


def main() -> None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("OPENROUTER_API_KEY not set — this optional eval needs an OpenRouter key.")
        print("Get one at https://openrouter.ai/keys, then:")
        print("  OPENROUTER_API_KEY=... python3 scripts/eval_openrouter_judge.py")
        # Still show the deterministic baseline so the run is not empty.
        srcs = [SourceFeatures(u, t, u, "content " * 80) for u, t, _ in AUDIT]
        rep = score_sources(srcs, "transformers", preset("project_kickstart")).as_dict()
        print(f"\n[deterministic baseline, no key needed] type_mix={rep['type_mix']} "
              f"type_diversity={rep['type_diversity']} quality={rep['quality_score']}")
        sys.exit(0)
    classification_agreement(key)


if __name__ == "__main__":
    main()
