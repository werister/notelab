"""semantic_term_expansions (research/semantic.py) — the real-embedding drop-in for
QualityWeights.term_expansions.

Offline tests mock the Mistral HTTP call (no network, no key, deterministic — these
run in CI). A separate live comparison test only runs when MISTRAL_API_KEY is set
(skipped otherwise) and makes a real call, per the task's request to "add a test
comparing lexical vs semantic coverage on a synonym-heavy fixture" against the
actual free-lane API, not a mock of it.
"""
import json
import os
import urllib.error
from unittest.mock import patch

import pytest

from notelab.research.ablation import quick_assess
from notelab.research.quality import QualityWeights, SourceFeatures, score_sources
from notelab.research.semantic import (
    MistralModelNotFoundError, SemanticExpansionError, _cosine, embed_texts,
    semantic_term_expansions,
)


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _with_models_listing(post_handler, *ids: str):
    """Wrap a fake urlopen so it serves the real endpoint pair embed_texts now uses:
    a GET /v1/models preflight, then the POST /v1/embeddings delegated to
    post_handler. `ids` is what the listing reports as available."""
    listed = ids or ("mistral-embed",)

    def _fake_urlopen(req, timeout=None):
        if req.get_method() == "GET":
            assert req.full_url.endswith("/v1/models"), f"unexpected preflight {req.full_url}"
            return _FakeResponse({"object": "list",
                                  "data": [{"id": i, "capabilities": "embed"} for i in listed]})
        return post_handler(req, timeout=timeout)

    return _fake_urlopen


def _fake_urlopen_factory(vectors_by_text: dict[str, list[float]], *listed_ids: str):
    """Returns a fake urlopen that inspects the request body and echoes back
    embeddings for exactly the texts it was asked for, in request order — mirrors
    the real API's contract without hitting the network."""
    def _post(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        texts = body["input"]
        data = [{"index": i, "embedding": vectors_by_text[t]} for i, t in enumerate(texts)]
        return _FakeResponse({"data": data, "model": body["model"]})
    return _with_models_listing(_post, *listed_ids)


def test_embed_texts_requires_api_key(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(SemanticExpansionError, match="MISTRAL_API_KEY"):
        embed_texts(["decoder"], api_key=None)


def test_embed_texts_empty_input_short_circuits_no_network(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    assert embed_texts([]) == []  # no key needed — nothing to embed, no call made


def test_embed_texts_raises_on_vector_count_mismatch():
    def bad_urlopen(req, timeout=None):
        return _FakeResponse({"data": [{"index": 0, "embedding": [0.1, 0.2]}]})
    with patch("urllib.request.urlopen", _with_models_listing(bad_urlopen)):
        with pytest.raises(SemanticExpansionError, match="expected 2 vectors"):
            embed_texts(["a", "b"], api_key="fake-key")


def test_embed_texts_reorders_by_index_defensively():
    def shuffled_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        # API returns them out of order; index field is authoritative
        return _FakeResponse({"data": [
            {"index": 1, "embedding": [0.0, 1.0]},
            {"index": 0, "embedding": [1.0, 0.0]},
        ]})
    with patch("urllib.request.urlopen", _with_models_listing(shuffled_urlopen)):
        vecs = embed_texts(["first", "second"], api_key="fake-key")
    assert vecs == [[1.0, 0.0], [0.0, 1.0]]


class _FakeHTTPError(urllib.error.HTTPError):
    def __init__(self, code: int, body: bytes = b""):
        super().__init__("https://api.mistral.ai/v1/models", code, "err", {}, None)
        self._body = body

    def read(self):
        return self._body


def test_model_id_is_resolved_against_live_listing_before_embeddings_call():
    """The preflight is a real GET /v1/models, and it happens *before* the
    embeddings POST — so a stale id never costs a wasted embeddings request."""
    calls = []

    def _post(req, timeout=None):
        calls.append(("POST", req.full_url))
        return _FakeResponse({"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

    def _fake_urlopen(req, timeout=None):
        if req.get_method() == "GET":
            calls.append(("GET", req.full_url))
            return _FakeResponse({"data": [{"id": "mistral-embed"}]})
        return _post(req, timeout=timeout)

    with patch("urllib.request.urlopen", _fake_urlopen):
        embed_texts(["decoder"], api_key="fake-key")

    assert calls == [("GET", "https://api.mistral.ai/v1/models"),
                     ("POST", "https://api.mistral.ai/v1/embeddings")]


def test_stale_model_id_raises_distinct_error_naming_available_models():
    """A stale MISTRAL_EMBED_MODEL is a config error, not an auth error — it gets its
    own exception type and names the ids that DO exist, instead of surfacing as a
    generic HTTP 400 from the embeddings endpoint that doesn't identify the model."""
    def _never_called(req, timeout=None):
        raise AssertionError(f"embeddings must not be called: {req.full_url}")

    with patch("urllib.request.urlopen",
               _with_models_listing(_never_called, "mistral-embed", "mistral-small-latest")):
        with pytest.raises(MistralModelNotFoundError) as exc:
            embed_texts(["decoder"], api_key="fake-key", model="mistral-embed-v0.1-stale")

    msg = str(exc.value)
    assert "mistral-embed-v0.1-stale" in msg
    assert "mistral-small-latest" in msg  # actionable: what to use instead
    assert "stale or misspelled" in msg


def test_model_not_found_is_still_a_semantic_expansion_error():
    """Contract preserved: the new subclass is caught by `except SemanticExpansionError`,
    so no existing caller can start seeing an uncaught exception or a silent empty dict."""
    assert issubclass(MistralModelNotFoundError, SemanticExpansionError)
    with pytest.raises(SemanticExpansionError):
        with patch("urllib.request.urlopen", _with_models_listing(
                lambda req, timeout=None: _FakeResponse({"data": []}))):
            embed_texts(["decoder"], api_key="fake-key")


def test_model_listing_auth_failure_is_not_reported_as_model_not_found():
    """A 401 on the *listing* is an auth problem. Reporting 'model not found' there
    would send the caller off to fix a model id that is probably fine."""
    def _fake_urlopen(req, timeout=None):
        raise _FakeHTTPError(401, b'{"message":"Unauthorized"}')

    with patch("urllib.request.urlopen", _fake_urlopen):
        with pytest.raises(SemanticExpansionError) as exc:
            embed_texts(["decoder"], api_key="bad-key")

    assert not isinstance(exc.value, MistralModelNotFoundError)
    assert "API key" in str(exc.value)


def test_model_listing_network_failure_is_not_reported_as_model_not_found():
    def _fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("Name or service not known")

    with patch("urllib.request.urlopen", _fake_urlopen):
        with pytest.raises(SemanticExpansionError) as exc:
            embed_texts(["decoder"], api_key="fake-key")

    assert not isinstance(exc.value, MistralModelNotFoundError)
    assert "model listing request failed" in str(exc.value)


def test_malformed_model_listing_is_not_reported_as_model_not_found():
    """A listing that doesn't parse is a shape bug on our side — 'not found' would be
    a false claim, so it stays a plain SemanticExpansionError."""
    def _fake_urlopen(req, timeout=None):
        return _FakeResponse({"unexpected": "shape"})

    with patch("urllib.request.urlopen", _fake_urlopen):
        with pytest.raises(SemanticExpansionError) as exc:
            embed_texts(["decoder"], api_key="fake-key")

    assert not isinstance(exc.value, MistralModelNotFoundError)
    assert "expected a 'data' list" in str(exc.value)


def test_semantic_term_expansions_propagates_model_not_found_not_empty_result():
    """End-to-end through the public entry point: a stale model id must still raise,
    never degrade into a silent empty expansions dict (which would read as 'nothing
    was semantically related' — a different and false claim)."""
    sources = [SourceFeatures(id="A", url="https://arxiv.org/a", content="mwpm " * 200)]
    with patch("urllib.request.urlopen",
               _with_models_listing(lambda req, timeout=None: pytest.fail("no embeddings call"),
                                    "mistral-embed")):
        with pytest.raises(MistralModelNotFoundError):
            semantic_term_expansions("decoder", sources, api_key="fake-key",
                                     model="mistral-embed-stale")


def test_cosine_similarity_basic():
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0  # zero vector: no crash, defined as 0


def test_semantic_term_expansions_links_question_term_to_close_corpus_terms():
    sources = [SourceFeatures(id="A", url="https://arxiv.org/a", content="mwpm surface codes " * 50)]
    # 'decoder' (question term) is semantically close to 'mwpm' (corpus term), not
    # to 'codes' — a fake embedding space set up to make that concrete and checkable.
    vectors = {
        "decoder": [1.0, 0.0],
        "mwpm": [0.95, 0.05],      # cos ~0.998 with 'decoder' — above threshold
        "surface": [0.0, 1.0],     # orthogonal — below threshold
        "codes": [0.1, 0.99],      # far from 'decoder' — below threshold
    }
    with patch("urllib.request.urlopen", _fake_urlopen_factory(vectors)):
        result = semantic_term_expansions("decoder", sources, threshold=0.9, api_key="fake-key")
    assert result.expansions == {"decoder": {"mwpm"}}
    assert result.question_terms == ["decoder"]


def test_semantic_term_expansions_no_question_terms_short_circuits():
    result = semantic_term_expansions("", [], api_key="fake-key")
    assert result.expansions == {}
    assert result.corpus_terms_considered == 0


def test_semantic_term_expansions_empty_corpus_short_circuits():
    result = semantic_term_expansions("decoder algorithm", [], api_key="fake-key")
    assert result.expansions == {}


def test_semantic_term_expansions_result_is_a_drop_in_for_quality_weights_term_expansions():
    """The whole point: the SAME QualityWeights interface, no changes anywhere else.
    A source using only the synonym 'mwpm' counts as covering 'decoder' once its
    expansions are plugged into term_expansions — exactly like the hand-configured
    map already works, just sourced from embeddings instead of a config file."""
    sources = [SourceFeatures(id="A", url="https://arxiv.org/a", content="mwpm " * 200)]
    vectors = {"decoder": [1.0, 0.0], "mwpm": [0.95, 0.05]}
    with patch("urllib.request.urlopen", _fake_urlopen_factory(vectors)):
        result = semantic_term_expansions("decoder", sources, threshold=0.9, api_key="fake-key")

    weights = QualityWeights(term_expansions=result.expansions)
    report = score_sources(sources, question="decoder", weights=weights)
    assert report.per_source[0].relevance == pytest.approx(1.0)  # via semantic expansion

    # without it, the same source scores 0 relevance — pure lexical miss (documented
    # limitation this module exists to fix):
    lexical_report = score_sources(sources, question="decoder", weights=QualityWeights())
    assert lexical_report.per_source[0].relevance == 0.0


@pytest.mark.skipif(not os.environ.get("MISTRAL_API_KEY"),
                    reason="live comparison needs a real MISTRAL_API_KEY")
def test_live_semantic_beats_lexical_coverage_on_synonym_heavy_fixture():
    """Real network call, real embedding model — not mocked. A source that only
    uses quantum-decoding jargon ('mwpm', 'surface code stabilizer syndrome') should
    be found semantically related to a plain-language question about 'decoder',
    where pure lexical coverage misses it entirely."""
    sources = [
        SourceFeatures(id="jargon", url="https://arxiv.org/x",
                       content="minimum weight perfect matching mwpm surface code "
                               "stabilizer syndrome extraction " * 30),
        SourceFeatures(id="offtopic", url="https://example.com/y",
                       content="sourdough bread baking hydration ratio " * 30),
    ]
    question = "quantum error correction decoder"

    lexical = quick_assess(sources, question, mode="fast")
    assert "decoder" in lexical["coverage_gaps"]  # pure lexical: jargon source not credited

    result = semantic_term_expansions(question, sources, threshold=0.72)
    assert result.expansions, "expected at least one real semantic link from the live API"
    semantic_weights = QualityWeights(term_expansions=result.expansions)
    semantic = quick_assess(sources, question, weights=semantic_weights, mode="fast")
    assert "decoder" not in semantic["coverage_gaps"]
    assert semantic["coverage"] > lexical["coverage"]
