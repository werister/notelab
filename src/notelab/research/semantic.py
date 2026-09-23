"""
Real semantic term expansion — a drop-in producer for `QualityWeights.term_expansions`.

`term_expansions` (quality.py) is a hand-configured `{term: {synonyms}}` map: deliberately
static and offline so the engine's stdlib-only default never needs it. This module computes
the SAME shape of dict from an actual embedding model instead of a hand-maintained list, so
a source counts as covering "decoder" because it's *semantically* close to "mwpm" or "surface
codes" — not because someone remembered to add that pair to a config file.

Gated, not default: nothing in quality.py/ablation.py/evolve.py imports this module. A caller
opts in explicitly by calling `semantic_term_expansions(...)` and passing the result as
`QualityWeights(term_expansions=...)` — the stdlib-only, no-network, no-key default path is
completely unaffected by this module existing.

Uses the Mistral embeddings API (`mistral-embed`, confirmed live: 1024-dim, real /v1/models
listing — not guessed) over stdlib `urllib`, so this adds zero new pip dependencies; the only
new requirement is a `MISTRAL_API_KEY` (or an explicit `api_key=`) at call time, and a network
call. No key -> SemanticExpansionError, not a silent empty-dict fallback: a caller who asked
for semantic expansion and got none back (because auth failed) must be told, not quietly
downgraded to lexical-only without knowing.
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from .quality import SourceFeatures, _content_terms

MISTRAL_EMBED_MODEL = "mistral-embed"
_EMBEDDINGS_URL = "https://api.mistral.ai/v1/embeddings"


class SemanticExpansionError(RuntimeError):
    """No key, a network failure, or a malformed API response. Never swallowed into
    an empty expansions dict — that would silently look identical to 'nothing was
    semantically related', which is a different (and false) claim."""


def _resolve_api_key(api_key: str | None) -> str:
    key = api_key or os.environ.get("MISTRAL_API_KEY")
    if not key:
        raise SemanticExpansionError(
            "semantic_term_expansions needs a Mistral API key — pass api_key= or set "
            "MISTRAL_API_KEY. (Free-lane embeddings, not part of the stdlib-only default.)")
    return key


def embed_texts(texts: list[str], api_key: str | None = None,
                model: str = MISTRAL_EMBED_MODEL, timeout: float = 30.0) -> list[list[float]]:
    """Embed a batch of strings via the Mistral embeddings API. Returns one vector per
    input, in the same order. Raises SemanticExpansionError on any failure — auth,
    network, or a response shape that doesn't match what we asked for."""
    if not texts:
        return []
    key = _resolve_api_key(api_key)
    body = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urllib.request.Request(
        _EMBEDDINGS_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise SemanticExpansionError(f"Mistral embeddings HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise SemanticExpansionError(f"Mistral embeddings request failed: {e.reason}") from e

    data = payload.get("data")
    if not isinstance(data, list) or len(data) != len(texts):
        raise SemanticExpansionError(
            f"Mistral embeddings: expected {len(texts)} vectors, got "
            f"{len(data) if isinstance(data, list) else type(data).__name__}")
    # the API returns items in request order with an "index" field; sort defensively
    # rather than assume order, so a future API change can't silently misalign terms.
    ordered = sorted(data, key=lambda d: d.get("index", 0))
    return [item["embedding"] for item in ordered]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


@dataclass
class SemanticExpansionResult:
    expansions: dict[str, set[str]]
    question_terms: list[str]
    corpus_terms_considered: int
    threshold: float

    def as_dict(self) -> dict:
        return {"expansions": {k: sorted(v) for k, v in self.expansions.items()},
                "question_terms": self.question_terms,
                "corpus_terms_considered": self.corpus_terms_considered,
                "threshold": self.threshold}


def semantic_term_expansions(
    question: str,
    sources: list[SourceFeatures],
    threshold: float = 0.78,
    max_corpus_terms: int = 150,
    api_key: str | None = None,
    model: str = MISTRAL_EMBED_MODEL,
) -> SemanticExpansionResult:
    """Compute a term_expansions-shaped dict by embedding the question's terms and a
    bounded sample of the corpus's own terms, then linking a question term to any
    corpus term above `threshold` cosine similarity (excluding the literal term
    itself, which coverage already matches lexically with no expansion needed).

    `max_corpus_terms` bounds the embedding call's cost/latency: corpus vocabulary
    can run into the thousands of unique terms across many sources, and embedding
    all of them on every call would make this expensive rather than the "cheap
    free-lane" it's meant to be. The most frequent terms across the corpus are kept
    — the ones actually likely to matter for coverage of a shared question.
    """
    qterms = sorted(_content_terms(question))
    if not qterms:
        return SemanticExpansionResult({}, [], 0, threshold)

    from collections import Counter
    freq: Counter[str] = Counter()
    for s in sources:
        freq.update(_content_terms(f"{s.title} {s.content}"))
    # question terms themselves don't need to be "discovered" in the corpus sample —
    # only compared against it — so rank the REST of the vocabulary by frequency.
    corpus_terms = [t for t, _ in freq.most_common() if t not in qterms][:max_corpus_terms]
    if not corpus_terms:
        return SemanticExpansionResult({}, qterms, 0, threshold)

    vectors = embed_texts(qterms + corpus_terms, api_key=api_key, model=model)
    q_vecs = dict(zip(qterms, vectors[:len(qterms)]))
    c_vecs = dict(zip(corpus_terms, vectors[len(qterms):]))

    expansions: dict[str, set[str]] = {}
    for qt, qv in q_vecs.items():
        related = {ct for ct, cv in c_vecs.items() if _cosine(qv, cv) >= threshold}
        if related:
            expansions[qt] = related
    return SemanticExpansionResult(expansions, qterms, len(corpus_terms), threshold)
